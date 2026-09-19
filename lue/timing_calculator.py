"""
Word-level timing calculation module for the Lue eBook reader.
"""

import logging
import re
import string
from functools import lru_cache
from typing import List, Tuple, Optional, Dict, Any

@lru_cache(maxsize=1024)
def _sanitize_word(word: str) -> str:
    """
    Sanitize a word by stripping all non-alphanumeric characters and converting to lowercase.
    
    This function is used for case-insensitive word comparison by removing punctuation
    and other special characters, leaving only letters and numbers.
    
    Args:
        word: The word to sanitize
        
    Returns:
        Sanitized word containing only lowercase alphanumeric characters
    """
    if not word:
        return ""
    
    # Strip all non-alphanumeric characters and convert to lowercase
    return re.sub(r'[^a-zA-Z0-9]', '', word).lower()


def _get_highlightable_words(text: str) -> list[str]:
    """
    Get list of words that should be considered for timing.
    
    This function filters out tokens that contain only punctuation/non-alphanumeric
    characters, which should not be counted as words for timing purposes.
    Strips punctuation (including commas from em dash replacement) for counting.
    
    Args:
        text: The text to process
        
    Returns:
        List of cleaned words that should be timed
    """
    if not text:
        return []
        
    # Split on whitespace to get tokens and filter efficiently
    words = []
    for token in text.split():
        cleaned = token.strip(string.punctuation)
        if cleaned:
            words.append(cleaned)
    
    return words


@lru_cache(maxsize=512)
def _extract_core_word(token: str) -> str:
    """
    Extract the core word from a token by removing surrounding punctuation.
    
    This function is more robust than simple strip() as it handles nested
    punctuation and preserves internal punctuation like contractions.
    
    Args:
        token: The token to process
        
    Returns:
        The core word without surrounding punctuation
    """
    if not token:
        return token
    
    start = 0
    while start < len(token) and not token[start].isalnum():
        start += 1
    
    end = len(token) - 1
    while end >= start and not token[end].isalnum():
        end -= 1
    
    return token[start:end + 1] if start <= end else ""


def create_word_mapping(original_words: List[str], tts_word_timings: List[Tuple[str, float, float]]) -> Optional[List[int]]:
    """
    Create a mapping from original word indices to TTS word timing indices.
    This handles cases where TTS combines words or processes punctuation differently.
    
    Uses sanitized word comparison with fuzzy matching to handle:
    - Punctuation differences between original and TTS text
    - Case differences
    - Word combining/splitting by TTS engines
    - Punctuation-only tokens
    
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
            logging.debug(f"create_word_mapping: Word {orig_index} '{orig_word}' -> TTS {last_tts_index} (exhausted TTS words)")
            continue
        
        orig_sanitized_word = orig_sanitized[orig_index]
        
        if not orig_sanitized_word:
            prev_mapping = mapping[-1] if mapping else 0
            mapping.append(prev_mapping)
            logging.debug(f"create_word_mapping: Word {orig_index} '{orig_word}' (punctuation-only) -> TTS {prev_mapping}")
            continue
        
        best_match_index = None
        best_match_score = 0
        search_range = min(tts_len, tts_index + 5)
        
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
            logging.debug(f"create_word_mapping: Word {orig_index} '{orig_word}' -> TTS {tts_index} (no match, fallback)")
            tts_index += 1
        else:
            mapping.append(best_match_index)
            logging.debug(f"create_word_mapping: Word {orig_index} '{orig_word}' -> TTS {best_match_index} '{tts_words[best_match_index]}' (score={best_match_score})")
            if best_match_index <= tts_index + 2:
                tts_index = best_match_index + 1
    
    if len(original_words) != tts_len:
        logging.debug(f"create_word_mapping: Word count mismatch - {len(original_words)} original vs {tts_len} TTS")
    
    return mapping


def _words_similar(word1: str, word2: str) -> bool:
    """
    Check if two words are similar (for handling slight differences in tokenization).
    
    Args:
        word1: First word to compare
        word2: Second word to compare
        
    Returns:
        True if words are similar, False otherwise
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
    Adjust word timings to ensure continuity and handle timing inconsistencies.
    
    Args:
        word_timings: List of (word, start_time, end_time) tuples
        
    Returns:
        Adjusted list of (word, start_time, end_time) tuples
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
                    end_time = (end_time + next_start_time) / 2
        
        adjusted_word_timings.append((word, start_time, end_time))
    
    return adjusted_word_timings


def calculate_speech_duration(word_timings: List[Tuple[str, float, float]]) -> float:
    """
    Calculate the total speech duration from word timings.
    
    Args:
        word_timings: List of (word, start_time, end_time) tuples
        
    Returns:
        Speech duration in seconds
    """
    if not word_timings:
        return 0.0
    
    return max((end for _, _, end in word_timings if end is not None), default=0.0)


def estimate_word_timings_from_duration(text: str, total_duration: float) -> List[Tuple[str, float, float]]:
    """
    Estimate word timings based on word count and total duration.
    This is a fallback when TTS doesn't provide precise timing information.
    
    Args:
        text: The text that was spoken
        total_duration: Total duration of the audio in seconds
        
    Returns:
        List of (word, start_time, end_time) tuples
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
    Process raw timing data from TTS into a standardized format with all necessary
    calculations and adjustments applied.
    
    Args:
        original_text: The original text that was spoken
        raw_word_timings: Raw word timings from TTS engine
        total_duration: Total audio duration (optional, will be calculated if not provided)
        
    Returns:
        Dictionary containing:
        - word_timings: Adjusted word timings
        - speech_duration: Duration of speech content
        - total_duration: Total audio duration
        - word_mapping: Mapping from original words to TTS timings
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
        
        if word_mapping:
            logging.debug(f"Created word mapping: {len(original_words)} original words -> {len(word_timings)} TTS timings")
            if len(original_words) != len(word_timings):
                logging.debug(f"Word count mismatch - Original: {original_words}, TTS: {[w for w, _, _ in word_timings]}")
        
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
    
    Args:
        timing_data: Dictionary containing timing information
        
    Returns:
        True if valid, False otherwise
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
