"""
Word-level timing calculation module for the Lue eBook reader.
"""

import logging
import re
import unicodedata
from functools import lru_cache
from typing import List, Tuple, Optional, Dict, Any

@lru_cache(maxsize=1024)
def _sanitize_word(word: str) -> str:
    """
    Sanitize a word by stripping all non-alphanumeric characters and converting to lowercase.
    
    Args:
        word: The word to sanitize
        
    Returns:
        Sanitized word containing only lowercase alphanumeric characters
    """
    if not word:
        return ""

    # Unicode : « été » reste « été » (l'ancien [^a-zA-Z0-9] donnait « t »,
    # « élève » → « lve », « français » → « franais »). NFC pour qu'une lettre
    # composée et décomposée comparent égales.
    word = unicodedata.normalize('NFC', word)
    return re.sub(r'[\W_]+', '', word).lower()


# Seuls les tirets longs/demi-cadratins séparent deux mots collés ("mot—mot").
# Le trait d'union fait partie du mot (« dit-elle », « peut-être », « a-t-il »).
_DASH_SPLIT = re.compile(r'([—–])')


def has_alnum(text: str) -> bool:
    """True si `text` contient au moins une lettre/un chiffre (Unicode)."""
    return any(ch.isalnum() for ch in text)


def split_highlight_units(text: str) -> List[str]:
    """
    Découpe `text` en unités de surlignage, avec leur ponctuation d'origine.

    C'est LA définition d'un « mot » partagée par le timing (word_mapping),
    reader.py (current_sentence_words) et ui.py (rendu du mot courant) :
    si ces trois endroits comptent différemment, le surlignage dérive.
    Avant : timing comptait « « » », reader ignorait tout ce qui n'est pas
    ASCII (« à » disparaissait) et ui coupait aux traits d'union.
    """
    if not text:
        return []
    units: List[str] = []
    for token in text.split():
        for part in _DASH_SPLIT.split(token):
            if part and has_alnum(part):
                units.append(part)
    return units


def _get_highlightable_words(text: str) -> List[str]:
    """
    Get list of words that should be considered for timing.

    Args:
        text: The text to process

    Returns:
        List of cleaned words that should be timed (same units, same order
        as split_highlight_units, without surrounding punctuation)
    """
    return [_extract_core_word(unit) for unit in split_highlight_units(text)]


@lru_cache(maxsize=512)
def _extract_core_word(token: str) -> str:
    """
    Extract the core word from a token by removing surrounding punctuation safely.
    """
    if not token:
        return ""
    
    start = 0
    while start < len(token) and not token[start].isalnum():
        start += 1
    
    end = len(token) - 1
    while end >= start and not token[end].isalnum():
        end -= 1
    
    if start > end:
        return ""
        
    return token[start:end + 1]


def create_word_mapping(original_words: List[str], tts_word_timings: List[Tuple[str, float, float]]) -> Optional[List[int]]:
    """
    Create a mapping from original word indices to TTS word timing indices.
    
    Args:
        original_words: List of words from the original text
        tts_word_timings: List of (word, start_time, end_time) tuples from TTS
    
    Returns:
        List where index i contains the TTS timing index for original word i,
        or None if no valid mapping can be created.
    """
    if not original_words or not tts_word_timings:
        logging.debug("create_word_mapping: Empty input - original_words or tts_word_timings is empty")
        return None
    
    tts_words = [word for word, _, _ in tts_word_timings]
    orig_sanitized = [_sanitize_word(word) for word in original_words]
    tts_sanitized = [_sanitize_word(word) for word in tts_words]
    
    # Fast path: 1:1 match optimization
    if len(original_words) == len(tts_words) and orig_sanitized == tts_sanitized:
        logging.debug(f"create_word_mapping: Perfect 1:1 match with {len(original_words)} words")
        return list(range(len(original_words)))
    
    mapping = []
    tts_index = 0
    tts_len = len(tts_words)
    
    logging.debug(f"create_word_mapping: Mapping {len(original_words)} original words to {tts_len} TTS words")
    
    for orig_index, orig_word in enumerate(original_words):
        if tts_index >= tts_len:
            last_tts_index = max(0, tts_len - 1)
            mapping.append(last_tts_index)
            continue
        
        orig_sanitized_word = orig_sanitized[orig_index]
        
        if not orig_sanitized_word:
            prev_mapping = mapping[-1] if mapping else 0
            mapping.append(prev_mapping)
            continue
        
        best_match_index = None
        best_match_score = 0
        search_range = min(tts_len, tts_index + 10)
        
        for search_idx in range(tts_index, search_range):
            tts_sanitized_word = tts_sanitized[search_idx]
            if not tts_sanitized_word:
                continue
            
            score = 0
            if orig_sanitized_word == tts_sanitized_word:
                score = 100
            elif orig_sanitized_word in tts_sanitized_word:
                score = 80
            elif tts_sanitized_word in orig_sanitized_word:
                score = 60
            elif _words_similar(orig_sanitized_word, tts_sanitized_word):
                score = 40
            
            if score > best_match_score:
                best_match_score = score
                best_match_index = search_idx
                if score == 100:
                    break
        
        if best_match_index is None or best_match_score == 0:
            mapping.append(tts_index)
            if any(ch.isdigit() for ch in orig_sanitized_word):
                # « 1998 » est dit « mille neuf cent quatre-vingt-dix-huit » :
                # plusieurs mots TTS pour un mot du texte. On resynchronise sur
                # le prochain mot du texte au lieu d'avancer d'un seul cran.
                resync = _find_resync_index(orig_sanitized, orig_index + 1, tts_sanitized, tts_index + 1)
                tts_index = resync if resync is not None else tts_index + 1
            else:
                tts_index += 1
        else:
            mapping.append(best_match_index)
            if best_match_index <= tts_index + 2:
                tts_index = best_match_index + 1
    
    return mapping


RESYNC_LOOKAHEAD = 20


def _find_resync_index(
    orig_sanitized: List[str],
    next_orig_index: int,
    tts_sanitized: List[str],
    tts_start: int,
) -> Optional[int]:
    """Index TTS du prochain mot (non numérique) du texte, ou None si introuvable."""
    target = None
    for word in orig_sanitized[next_orig_index:]:
        if word and not any(ch.isdigit() for ch in word):
            target = word
            break
    if target is None:
        return None
    stop = min(len(tts_sanitized), tts_start + RESYNC_LOOKAHEAD)
    for idx in range(tts_start, stop):
        if tts_sanitized[idx] == target:
            return idx
    return None


@lru_cache(maxsize=256)
def _words_similar(word1: str, word2: str) -> bool:
    """
    Check if two words are similar (cached for performance).
    """
    if not word1 or not word2:
        return False
    
    len1, len2 = len(word1), len(word2)
    
    if len1 >= 3 and len2 >= 3 and (word1 in word2 or word2 in word1):
        return True
    
    if len1 >= 4 and len2 >= 4 and word1[:3] == word2[:3] and abs(len1 - len2) <= 2:
        return True
    
    return False


def adjust_word_timings_for_continuity(word_timings: List[Tuple[str, float, float]]) -> List[Tuple[str, float, float]]:
    """
    Adjust word timings to ensure continuity and handle timing inconsistencies safely.
    """
    if len(word_timings) <= 1:
        return word_timings
    
    cleaned_timings = []
    for word, start_time, end_time in word_timings:
        if start_time is None or end_time is None:
            cleaned_timings.append((word, start_time, end_time))
            continue
            
        if end_time < start_time:
            end_time = (start_time + 0.1) if start_time > 0 else (end_time + 0.1)
        
        if end_time - start_time < 0.05:
            end_time = start_time + 0.05
            
        cleaned_timings.append((word, start_time, end_time))
    
    adjusted_word_timings = []
    timings_len = len(cleaned_timings)
    
    for i in range(timings_len):
        word, start_time, end_time = cleaned_timings[i]
        
        if start_time is None or end_time is None:
            adjusted_word_timings.append((word, start_time, end_time))
            continue
        
        if i < timings_len - 1:
            _, next_start_time, _ = cleaned_timings[i + 1]
            if next_start_time is not None:
                if next_start_time > end_time:
                    end_time = next_start_time
                elif next_start_time < end_time:
                    # Alignement strict pour préserver la consistance des durées
                    end_time = next_start_time
        
        adjusted_word_timings.append((word, start_time, end_time))
    
    return adjusted_word_timings


def calculate_speech_duration(word_timings: List[Tuple[str, float, float]]) -> float:
    """
    Calculate the total speech duration from word timings safely.
    """
    if not word_timings:
        return 0.0
    
    valid_ends = [end for _, _, end in word_timings if end is not None]
    return max(valid_ends, default=0.0)


def estimate_word_timings_from_duration(text: str, total_duration: float) -> List[Tuple[str, float, float]]:
    """
    Estimate word timings based on word count and total duration.
    """
    words = _get_highlightable_words(text)
    if not words:
        return []
    
    word_count = len(words)
    if not total_duration or total_duration <= 0:
        total_duration = word_count * 0.3
    
    time_per_word = total_duration / word_count
    return [(word, i * time_per_word, (i + 1) * time_per_word) for i, word in enumerate(words)]


def process_tts_timing_data(
    original_text: str,
    raw_word_timings: List[Tuple[str, float, float]],
    total_duration: Optional[float] = None
) -> Dict[str, Any]:
    """
    Process raw timing data from TTS into a standardized format.
    """
    try:
        if not raw_word_timings:
            if total_duration is None:
                logging.warning("No timing data and no duration provided, using fallback estimation")
                total_duration = len(original_text.split()) * 0.3
            
            word_timings = estimate_word_timings_from_duration(original_text, total_duration)
            speech_duration = total_duration
        else:
            logging.debug(f"Processing {len(raw_word_timings)} raw timing entries for text: '{original_text[:50]}...'")
            word_timings = adjust_word_timings_for_continuity(raw_word_timings)
            speech_duration = calculate_speech_duration(word_timings)
        
        original_words = _get_highlightable_words(original_text)
        word_mapping = create_word_mapping(original_words, word_timings)
        
        final_total_duration = total_duration if total_duration is not None else speech_duration
        
        return {
            "word_timings": word_timings,
            "speech_duration": speech_duration,
            "total_duration": final_total_duration,
            "word_mapping": word_mapping
        }
        
    except Exception as e:
        logging.error(f"Error processing TTS timing data: {e}", exc_info=True)
        fallback_words = _get_highlightable_words(original_text)
        fallback_duration = len(fallback_words) * 0.3
        fallback_timings = estimate_word_timings_from_duration(original_text, fallback_duration)
        
        return {
            "word_timings": fallback_timings,
            "speech_duration": fallback_duration,
            "total_duration": fallback_duration,
            "word_mapping": create_word_mapping(fallback_words, fallback_timings)
        }


def validate_timing_data(timing_data: Dict[str, Any]) -> bool:
    """
    Validate that timing data contains all required fields and is properly formatted.
    """
    if not isinstance(timing_data, dict):
        return False
        
    required_fields = ["word_timings", "speech_duration", "total_duration"]
    if not all(field in timing_data for field in required_fields):
        return False
    
    word_timings = timing_data["word_timings"]
    if not isinstance(word_timings, list):
        return False
    
    for timing in word_timings:
        if not isinstance(timing, tuple) or len(timing) != 3:
            return False
        word, start_time, end_time = timing
        if not isinstance(word, str):
            return False
        if start_time is not None and not isinstance(start_time, (int, float)):
            return False
        if end_time is not None and not isinstance(end_time, (int, float)):
            return False
        if isinstance(start_time, (int, float)) and start_time < 0:
            return False
        if (isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)) and 
            end_time < start_time):
            return False
    
    return True