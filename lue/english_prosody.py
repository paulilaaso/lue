"""
Moteur de prosodie anglaise (US/UK) pour LUE (lue-reader)
Améliore significativement la qualité de synthèse vocale en anglais.

Features:
- Gestion intelligente des pauses et accents
- Normalisation typographique anglaise
- Emphase sur mots-clés anglais (connecteurs, intensificateurs)
- Division de phrases robuste
- Support des abréviations courantes (US/UK)

Usage:
    from english_prosody import get_english_prosody_engine
    
    engine = get_english_prosody_engine()
    enhanced_text = engine.enhance_sentence_for_tts("Hello, how are you?")
    print(enhanced_text)
"""

import re
import logging
from typing import List, Tuple, Dict, Optional, Pattern
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProsodicEvent:
    """Événement de prosodie."""
    event_type: str  # "pause", "emphasis", "speed_change"
    position: int    # Position dans le texte
    duration: float  # Durée (secondes)
    word: str        # Mot affecté


class EnglishProsodyEngine:
    """
    Moteur de prosodie pour synthèse vocale anglaise (US/UK).
    
    Traite le texte avant envoi au TTS pour améliorer la qualité vocale.
    """
    
    def __init__(self):
        """Initialise le moteur avec les règles anglaises."""
        
        # Mots anglais qui nécessitent une emphase naturelle
        self.emphasis_words = {
            # Connecteurs logiques
            "however", "therefore", "thus", "nevertheless", "otherwise",
            "yet", "meanwhile", "furthermore", "moreover", "consequently",
            
            # Intensificateurs
            "really", "absolutely", "certainly", "obviously",
            "definitely", "truly", "surely", "clearly", "extremely",
            
            # Négations fortes
            "not", "never", "none", "neither", "nobody", "nothing",
            
            # Affirmations / transitions
            "yes", "indeed", "also", "still", "already", "always",
            
            # Mots de transition structurants
            "first", "second", "third", "firstly", "secondly",
            "finally", "subsequently", "meanwhile",
        }
        
        # Abréviations anglaises courantes (US & UK)
        self.abbreviations = {
            r'\bMr\.': 'Mister',
            r'\bMrs\.': 'Mistress',
            r'\bMs\.': 'Ms',
            r'\bDr\.': 'Doctor',
            r'\bProf\.': 'Professor',
            r'\bRev\.': 'Reverend',
            r'\bJr\.': 'Junior',
            r'\bSr\.': 'Senior',
            r'\bVs\.': 'versus',
            r'\bvs\.': 'versus',
            r'\be\.g\.': 'for example',
            r'\bi\.e\.': 'that is',
            r'\betc\.': 'et cetera',
            r'\bEtc\.': 'et cetera',
            r'\bapprox\.': 'approximately',
            r'\bdept\.': 'department',
            r'\bltd\.': 'limited',
            r'\binc\.': 'incorporated',
        }
        
        # Ponctuation anglaise : silence requis (millisecondes)
        self.silence_after_punctuation = {
            '.': 500,      # Point simple
            '…': 800,      # Ellipsis (longue pause)
            '!': 600,      # Exclamation
            '?': 600,      # Question
            ':': 300,      # Two-points
            ';': 400,      # Semicolon
            ',': 150,      # Comma
            '"': 200,      # Double quotes
            "'": 50,       # Single quote / apostrophe
        }
        
        # Patterns de phrases
        self.sentence_end_pattern = re.compile(r'[.!?…]')
        self.word_pattern = re.compile(r"\b\w+(?:['-]\w+)?\b")  # Gère apostrophes et traits d'union
        
        # Cache patterns compilés
        self._emphasis_pattern = None
    
    def get_emphasis_pattern(self) -> Pattern:
        """Retourne le pattern regex compilé pour les mots d'emphase."""
        if self._emphasis_pattern is None:
            words = '|'.join(re.escape(w) for w in self.emphasis_words)
            self._emphasis_pattern = re.compile(
                rf'\b({words})\b',
                re.IGNORECASE | re.UNICODE
            )
        return self._emphasis_pattern
    
    def preprocess_text(self, text: str) -> str:
        """
        Prétraite le texte anglais.
        
        Améliorations :
        - Normalise les espaces et caractères spéciaux
        - Uniformise les apostrophes anglaises droites et courbes (' et ’)
        - Normalise les ellipses (...)
        - Nettoie les tirets cadratins et espacements
        """
        
        if not text or not isinstance(text, str):
            return ""
        
        # 1. Normaliser espaces insécables
        text = text.replace('\u00a0', ' ')
        
        # 2. Uniformiser les apostrophes (standard en anglais)
        text = text.replace('’', "'").replace('ʼ', "'")

        # 3. Uniformiser les ellipses
        text = re.sub(r'…|\.{3,}', '...', text)

        # 4. Gérer les tirets longs (em-dash / en-dash) fréquents en anglais
        text = text.replace('—', ', ').replace('–', ', ')

        # 5. Espacement correct autour de la ponctuation anglaise
        text = re.sub(r'\s+,', ',', text)
        text = re.sub(r'(\S)([!?])', r'\1 \2', text)
        text = re.sub(r'(\S):', r'\1 :', text)
        text = re.sub(r'(\S);', r'\1 ;', text)

        # 6. Nettoyer espaces multiples
        text = re.sub(r'\s{2,}', ' ', text)
        
        return text.strip()
    
    def prepare_for_tts(self, sentence: str) -> str:
        """Apply only timing-safe English normalization before TTS.

        Deliberately does not expand abbreviations or rewrite numbers/dates to 
        prevent breaking word-level highlighting sync.
        """
        return self.preprocess_text(sentence)

    def normalize_abbreviations(self, text: str) -> str:
        """Remplace les abréviations par leurs formes complètes."""
        for pattern, replacement in self.abbreviations.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text
    
    def split_sentences(self, text: str) -> List[str]:
        """Divise intelligemment le texte en phrases anglaises."""
        if text and text[-1] not in '.!?…':
            text = text + '.'
        
        pattern = r'(?<=[.!?…])\s+(?=[A-Z0-9])|(?<=[.!?…])$'
        sentences = re.split(pattern, text)
        
        return [s.strip() for s in sentences if s.strip()]
    
    def is_emphasis_word(self, word: str) -> bool:
        """Vérifie si le mot doit être mis en avant."""
        return word.lower() in self.emphasis_words
    
    def apply_emphasis_to_words(self, text: str) -> Tuple[str, List[Tuple[str, int]]]:
        """Identifie les mots à mettre en avant."""
        emphasis_positions = []
        pattern = self.get_emphasis_pattern()
        
        for match in pattern.finditer(text):
            word = match.group(0)
            start = match.start()
            emphasis_positions.append((word, start))
        
        return text, emphasis_positions
    
    def calculate_pause_after_punctuation(self, char: str) -> float:
        """Retourne la durée de pause requise (secondes) après ponctuation."""
        duration_ms = self.silence_after_punctuation.get(char, 0)
        return duration_ms / 1000.0
    
    def enhance_sentence_for_tts(self, sentence: str, tts_engine: str = "edge") -> str:
        """Améliore une phrase anglaise pour un rendu TTS optimal."""
        if not sentence or not isinstance(sentence, str):
            return ""
        
        enhanced = self.preprocess_text(sentence)
        enhanced = self.normalize_abbreviations(enhanced)
        
        return enhanced
    
    def get_prosodic_events(self, text: str) -> List[ProsodicEvent]:
        """Analyse le texte et retourne les événements de prosodie."""
        events = []
        
        for match in re.finditer(r'[.!?…:;,]', text):
            char = match.group(0)
            duration = self.calculate_pause_after_punctuation(char)
            if duration > 0:
                events.append(ProsodicEvent(
                    event_type="pause",
                    position=match.start(),
                    duration=duration,
                    word=char
                ))
        
        _, emphasis_positions = self.apply_emphasis_to_words(text)
        for word, pos in emphasis_positions:
            events.append(ProsodicEvent(
                event_type="emphasis",
                position=pos,
                duration=0.1,
                word=word
            ))
        
        events.sort(key=lambda e: e.position)
        return events

    def validate_english_text(self, text: str) -> Dict[str, any]:
        """Valide et analyse un texte en anglais."""
        issues = []
        recommendations = []
        
        if not text:
            return {"valid": False, "issues": ["Empty text"], "recommendations": []}
        
        if '«' in text or '»' in text:
            recommendations.append("French-style guillemets detected (English typically uses quotes)")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "recommendations": recommendations,
            "char_count": len(text),
            "word_count": len(text.split()),
        }


# Singleton global
_english_prosody_engine: Optional[EnglishProsodyEngine] = None


def get_english_prosody_engine() -> EnglishProsodyEngine:
    """Retourne l'instance unique du moteur de prosodie anglaise."""
    global _english_prosody_engine
    if _english_prosody_engine is None:
        _english_prosody_engine = EnglishProsodyEngine()
        logger.info("English Prosody Engine initialized")
    return _english_prosody_engine


def reset_engine():
    """Réinitialise l'engine (pour tests)."""
    global _english_prosody_engine
    _english_prosody_engine = None