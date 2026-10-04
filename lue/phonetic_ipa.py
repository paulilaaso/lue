"""
Module de Phonétisation IPA (International Phonetic Alphabet)
════════════════════════════════════════════════════════════════════════════════
Convertit texte français en symboles phonétiques IPA complets, intégrant
la gestion robuste des liaisons et des cas particuliers ($H$ aspiré).

Références:
- Académie Française (Dictionnaire d'Orthoépie)
- CFPP2000 (Corpus de Français Parlé Parisien)
- Handbook of the International Phonetic Association
- Grevisse, Le Bon Usage (phonétique française)

Exemple:
    >>> phonetizer = FrenchIPAPhoneticizer()
    >>> phonetizer.text_to_ipa("extraordinaire")
    '/ɛkstʁaɔʁdinɛʁ/'
    
    >>> phonetizer.get_phonemes("extraordinaire")
    [
        {'grapheme': 'ex', 'ipa': 'ɛk', 'syllable': 1},
        {'grapheme': 'tra', 'ipa': 'tʁa', 'syllable': 2},
        ...
    ]
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import json

logger = logging.getLogger(__name__)


class VoiceType(Enum):
    """Type de voix pour prédictions."""
    MALE = "male"          # Voix masculine (moyenne ~120Hz)
    FEMALE = "female"      # Voix féminine (moyenne ~220Hz)
    NEUTRAL = "neutral"    # Neutre


@dataclass
class PhoneticSegment:
    """Segment phonétique d'un mot."""
    grapheme: str           # Lettres originales (ex: "ex")
    ipa: str                # Symboles IPA (ex: "ɛk")
    syllable_number: int    # Numéro de syllabe
    is_stressed: bool = False  # Accentuée?
    vowel_formants: Dict[str, float] = field(default_factory=dict)  # F1, F2, F3 (Hz)


@dataclass
class IPAAnalysis:
    """Résultat complet d'une analyse IPA."""
    original_text: str
    ipa_full: str           # Transcription IPA complète
    segments: List[PhoneticSegment] = field(default_factory=list)
    syllable_count: int = 0
    consonants: List[str] = field(default_factory=list)
    vowels: List[str] = field(default_factory=list)
    features: Dict[str, str] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════════════════
# CLASSES PHONÉTIQUES
# ════════════════════════════════════════════════════════════════════════════════

class ConsonantType(Enum):
    """Classification des consonnes."""
    STOP = "stop"              # p, t, k, b, d, g
    FRICATIVE = "fricative"    # f, v, s, z, ʃ, ʒ, x, ɣ
    AFFRICATE = "affricate"    # tʃ, dʒ (non-standard en français)
    NASAL = "nasal"            # m, n, ɲ, ŋ
    LATERAL = "lateral"        # l
    APPROXIMANT = "approximant"  # w, j, ɥ, ʁ
    TRILL = "trill"            # r (rare en français moderne)


class VowelQuality(Enum):
    """Qualité vocalique."""
    FRONT = "front"        # i, e, ɛ, a, y, ø, œ
    CENTRAL = "central"    # ə, ɑ̃
    BACK = "back"          # u, o, ɔ, ɑ, ɒ
    
    
class Nasality(Enum):
    """Nasalité des voyelles."""
    ORAL = "oral"          # a, e, i, o, u, y, ø, œ
    NASAL = "nasal"        # ɑ̃, ɛ̃, œ̃, ɔ̃


# ════════════════════════════════════════════════════════════════════════════════
# MOTEUR DE PHONÉTISATION IPA
# ════════════════════════════════════════════════════════════════════════════════

class FrenchIPAPhoneticizer:
    """
    Convertisseur robuste français → IPA.
    
    Utilise lexique complet + règles phonétiques pour transcription précise.
    """
    
    def __init__(self, voice_type: VoiceType = VoiceType.NEUTRAL):
        """
        Initialise le phonétiseur.
        
        Args:
            voice_type: Type de voix (pour futures extensions)
        """
        self.voice_type = voice_type
        self._initialize_dictionaries()
        self._initialize_rules()
        logger.info(f"Phonétiseur IPA initialisé (voix: {voice_type.value})")
    
    def _initialize_dictionaries(self) -> None:
        """Initialise les dictionnaires phonétiques, de liaisons et de h aspiré."""
        
        # ═════════════════════════════════════════════════════════════════
        # VOYELLES ORALES
        # ═════════════════════════════════════════════════════════════════
        self.vowels_oral = {
            'u': 'y',       # tu
            'eu': 'ø',      # peu, deux
            'œu': 'œ',      # peur
            'i': 'i',       # si, ici
            'y': 'i',       # psy → psi (variante)
            'ou': 'u',      # vous, tout
            'oo': 'u',      # zoo
            'é': 'e',       # été, café
            'e': 'e',       # été
            'è': 'ɛ',       # mère, très
            'ê': 'ɛ',       # être, fête
            'ai': 'ɛ',      # ai, aime
            'ei': 'ɛ',      # beige, veine
            'o': 'o',       # eau, beau
            'ô': 'o',       # château, pôle
            'au': 'o',      # eau, beau
            'eau': 'o',     # eau, beau
            'a': 'a',       # chat, papa
            'â': 'a',       # pâte, âne
        }
        
        # ═════════════════════════════════════════════════════════════════
        # VOYELLES NASALES
        # ═════════════════════════════════════════════════════════════════
        self.vowels_nasal = {
            'an': 'ɑ̃',      # an, dans, grand
            'am': 'ɑ̃',      # ambiance
            'en': 'ɑ̃',      # en, lent
            'em': 'ɑ̃',      # ensemble, temple
            'in': 'ɛ̃',      # in, pain, main
            'im': 'ɛ̃',      # immanent
            'yn': 'ɛ̃',      # lyn
            'un': 'ɛ̃',      # un, brun, parfum
            'um': 'ɛ̃',      # umami
            'ain': 'ɛ̃',     # pain, main
            'aim': 'ɛ̃',     # faim
            'ein': 'ɛ̃',     # rein, plein
            'on': 'ɔ̃',      # on, son, bon
            'om': 'ɔ̃',      # homme, pomme
            'oin': 'wɛ̃',    # loin, coin, point
            'ung': 'ɛ̃',     # Jung
        }
        
        # ═════════════════════════════════════════════════════════════════
        # CONSONNES
        # ═════════════════════════════════════════════════════════════════
        self.consonants = {
            'p': 'p',       # pas
            'b': 'b',       # bas
            't': 't',       # tas
            'd': 'd',       # da
            'k': 'k',       # casse
            'c': 'k',       # casse (sauf c+e/i)
            'g': 'g',       # gaz
            'qu': 'k',      # qui
            'q': 'k',       # Iraq
            'f': 'f',       # feu
            'v': 'v',       # veu
            's': 's',       # sou
            'ss': 's',      # assez
            'z': 'z',       # zèbre
            'ç': 's',       # ça, français
            'x': 'ks',      # axe, taxi
            'ex': 'ɛks',    # exemple
            'ch': 'ʃ',      # chat
            'sch': 'ʃ',     # schwa
            'j': 'ʒ',       # je
            'ge': 'ʒ',      # George
            'gi': 'ʒ',      # giraffe
            'h': '',        # h muet
            'w': 'w',       # wagon
            'm': 'm',       # me
            'n': 'n',       # ne
            'nn': 'n',      # nana
            'gn': 'ɲ',      # gnome
            'ni': 'ɲ',      # ni (avant voyelle)
            'l': 'l',       # le
            'll': 'l',      # elle
            'r': 'ʁ',       # rat
            'rr': 'ʁ',      # erre
            'y': 'j',       # yoga
            '\'': '',       # apostrophe
        }
        
        # ═════════════════════════════════════════════════════════════════
        # DIGRAPHES & TRIGRAPHES
        # ═════════════════════════════════════════════════════════════════
        self.digraphs = {
            'ch': 'ʃ',      # chat
            'ph': 'f',      # photo
            'gh': '',       # gh muet
            'th': 't',      # théâtre
            'rh': 'ʁ',      # rhume
            'qu': 'k',      # qui
            'gn': 'ɲ',      # gnome
            'ng': 'ŋ',      # parking
            'gu': 'g',      # guerre
            'ai': 'ɛ',      # aime
            'au': 'o',      # eau
            'ea': 'o',      # beau
            'ei': 'ɛ',      # beige
            'eu': 'ø',      # peu
            'ie': 'i',      # client
            'oi': 'wa',     # roi
            'ou': 'u',      # vous
            'oy': 'wa',     # royal
            'ue': 'y',      # rue
            'ui': 'ɥi',     # lui
            'ye': 'i',      # yeux
            'ya': 'ja',     # yoga
        }
        
        # ═════════════════════════════════════════════════════════════════
        # LIAISONS ET H ASPIRÉ ÉTENDUS
        # ═════════════════════════════════════════════════════════════════
        self.LIAISON_MAP_EXTENDED = {
            # /z/ - Pluriels et démonstratifs
            "les": "z", "des": "z", "ces": "z", "mes": "z", "tes": "z", 
            "ses": "z", "nos": "z", "vos": "z", "leurs": "z", "aux": "z", 
            "tous": "z", "quelques": "z", "autres": "z", "plusieurs": "z", 
            "divers": "z", "certains": "z",
            
            # /n/ - Articles et quantificateurs
            "un": "n", "aucun": "n", "aucune": "n", "mon": "n", "ton": "n", 
            "son": "n", "bon": "n", "bien": "n", "en": "n", "on": "n", 
            "rien": "n", "ancien": "n", "ancienne": "n", "certain": "n", 
            "certaine": "n", "plein": "n", "divin": "n",
            
            # /t/ - Adjectifs et adverbes
            "tout": "t", "petit": "t", "grand": "t", "comment": "t", 
            "est": "t", "très": "t", "compétent": "t", "important": "t", 
            "différent": "t", "courant": "t", "durant": "t", "pendant": "t", 
            "instant": "t", "apparent": "t", "emplacement": "t", 
            "équipement": "t", "penchant": "t",
            
            # /ʁ/ - Adjectifs avec -ier/-ère/-eur/-re
            "premier": "ʁ", "dernière": "ʁ", "derniere": "ʁ", "meilleur": "ʁ", 
            "meilleure": "ʁ", "antérieur": "ʁ", "extérieur": "ʁ", 
            "intérieur": "ʁ", "supérieur": "ʁ", "inférieur": "ʁ",
            
            # /p/ - Adverbes de quantité
            "beaucoup": "p", "trop": "p",
            
            # /d/ - Adjectifs commençant par gr- ou f-
            "fond": "d",
            
            # /s/ - Cas particuliers
            "moins": "s", "fois": "z",
            
            # /g/ - Littéraire
            "sang": "g",
        }

        self.H_ASPIRE_EXTENDED = {
            "héros", "haricot", "haricots", "hache", "haine", "hauteur", "honte",
            "hibou", "hiver", "hors", "huit", "huile", "humain", "humour",
            "hurler", "hâter", "harceler", "harasser", "heurter", "hocher",
            "haut", "hautain", "hasard", "hardi", "hareng", "harmonie", "harnais",
            "harpie", "hasardeux", "hauban", "haubannage", "haubert", "hausse",
            "haussement", "havane", "havre", "hébraïque", "hébreu", "hécatombe",
            "hématite", "hémicycle", "hémophile", "hémorragie", "hémorroïde",
            "henne", "héraldique", "héraut", "herbe", "herborisateur", "herbicide",
            "hercule", "hercynien", "hère", "hérédité", "hérés", "hérésiarque",
            "hérésie", "hérétique", "hérétiquement", "héretondelle", "héreuille",
            "herge", "hérissement", "hérisser", "hérisson", "hérite", "hériter",
            "héritière", "hermandad", "hermitage", "hermite", "hermitique",
            "hermès", "hermétique", "hermétiquement", "hermétisme", "hermione",
            "hermodactyle", "hermondactor", "hermopolite", "hermosa", "hermule",
            "hernade", "hernaire", "herniaire", "hernie", "hernié", "hernieux",
            "héro", "héroïde", "héroïquement", "héroïsme", "héron", "héronière",
            "héronniaire", "herpès", "herpestidé", "herpétologie", "herpétologue",
            "herppe", "herr", "herse", "herseau", "hersiere", "hersécher",
            "hersher", "herstaille", "hertfordshire", "héruque", "hervelette",
            "hervelle", "hérvens", "heryage", "hésitamment", "hésitance",
            "hésitant", "hésitation", "hésiter", "hétaire", "hétérocène",
            "hétérodoxe", "hétérododoxie", "hétérogamie", "hétérogène",
            "hétérogénéité", "hétérographe", "hétérographie", "hétérométrie",
            "hétéromorphe", "hétéromorphie", "hétéronyme", "hétéronymie",
            "hétéronyque", "hétéropathe", "hétéropathie", "hétérophage",
            "hétérophagie", "hétérophile", "hétérophilité", "hétérophonie",
            "hétérophoniquement", "hétérophoniste", "hétérophylle", "hétérophyllé",
            "hétérophyllie", "hétéroplasie", "hétéroplaste", "hétéropneuste", "hétéropode"
        }

        # Rétrocompatibilité interne au linker
        self.LIAISON_MAP = {
            "les": "z", "des": "z", "ces": "z", "un": "n",
            "aucun": "n", "tout": "t", "petit": "t", "grand": "t", "premier": "ʁ",
        }
        self.H_ASPIRE = set(self.H_ASPIRE_EXTENDED)
        self.FORBIDDEN_LIAISON_AFTER = {"et", "ou", "mais", "donc", "or", "ni", "car"}
        self.VOWELS = set("aeiouyàâäéèêëïîôöùûüœæ")

        # ═════════════════════════════════════════════════════════════════
        # DICTIONNAIRE DE MOTS SPÉCIAUX
        # ═════════════════════════════════════════════════════════════════
        self.special_words = {
            'monsieur': 'məsjø',
            'madame': 'madam',
            'mademoiselle': 'madmwazɛl',
            'oui': 'wi',
            'non': 'nɔ̃',
            'pas': 'pa',
            'très': 'tʁɛ',
            'plus': 'ply',
            'moins': 'mwɛ̃',
            'beaucoup': 'boku',
            'aujourd\'hui': 'oʒuʁdhɥi',
            'hier': 'jɛʁ',
            'demain': 'dəmɛ̃',
            'week-end': 'wikɛnd',
            'football': 'futbol',
            'email': 'imɛjl',
            'internet': 'intɛʁnɛt',
        }
        
        logger.debug("Dictionnaires phonétiques initialisés")
    
    def _initialize_rules(self) -> None:
        """Initialise les règles phonétiques contextuelles."""
        self.rules = {
            'rule_s_intervocalic': {
                'pattern': r'([aeiouy])s([aeiouy])',
                'replacement': r'\1z\2',
                'description': 'S entre deux voyelles → z'
            },
            'rule_c_soft': {
                'pattern': r'c(?=[eiy])',
                'replacement': 's',
                'description': 'C avant e, i, y → s'
            },
            'rule_c_hard': {
                'pattern': r'c(?=[aou])',
                'replacement': 'k',
                'description': 'C avant a, o, u → k'
            },
            'rule_g_soft': {
                'pattern': r'g(?=[eiy])',
                'replacement': 'ʒ',
                'description': 'G avant e, i, y → ʒ'
            },
            'rule_g_hard': {
                'pattern': r'g(?=[aou])',
                'replacement': 'g',
                'description': 'G avant a, o, u → g'
            },
            'rule_x_cons': {
                'pattern': r'x(?=[bdfghjklmnprstvwxz])',
                'replacement': 'ks',
                'description': 'X avant consonne → ks'
            },
            'rule_h_aspire': {
                'pattern': r'\bh',
                'replacement': 'ʔ',
                'description': 'H aspiré → glottal stop'
            },
        }
        
        logger.debug("Règles phonétiques initialisées")
    
    # ════════════════════════════════════════════════════════════════════
    # MÉTHODES PRINCIPALES DE CONVERSION ET LIAISONS
    # ════════════════════════════════════════════════════════════════════
    
    @lru_cache(maxsize=1024)
    def text_to_ipa(self, text: str) -> str:
        """
        Convertit texte français complet en IPA avec gestion des liaisons.
        
        Args:
            text: Texte à convertir
            
        Returns:
            Transcription IPA complète (ex: '/ɛkstʁaɔʁdinɛʁ/')
        """
        try:
            if not text or not isinstance(text, str):
                return ""
            
            clean_text = text.lower().strip()
            
            # Remplace mots spéciaux d'abord
            for word, ipa in self.special_words.items():
                clean_text = re.sub(rf'\b{word}\b', ipa, clean_text, flags=re.IGNORECASE)
            
            # Extraction des mots pour appliquer les règles de liaison
            words = clean_text.split()
            analyses = [self._convert_word_to_ipa(w) for w in words]
            
            # Application des liaisons contextuelles étendues
            linked_analyses = self.link_words(words, analyses)
            
            result = ''.join(linked_analyses)
            logger.debug(f"'{text}' → '{result}'")
            return f"/{result}/"
        
        except Exception as e:
            logger.error(f"Erreur conversion IPA: {e}")
            return ""
    
    def _convert_word_to_ipa(self, word: str) -> str:
        """Convertit un mot isolé en IPA."""
        if not word:
            return ""
        
        word = word.lower().strip()
        result = ""
        i = 0
        
        while i < len(word):
            converted = False
            
            # Trigraphes
            if i + 3 <= len(word):
                trigraph = word[i:i+3]
                if trigraph in self.digraphs:
                    result += self.digraphs[trigraph]
                    i += 3
                    converted = True
            
            # Digraphes
            if not converted and i + 2 <= len(word):
                digraph = word[i:i+2]
                if digraph in self.vowels_nasal:
                    result += self.vowels_nasal[digraph]
                    i += 2
                    converted = True
                elif digraph in self.digraphs:
                    result += self.digraphs[digraph]
                    i += 2
                    converted = True
            
            # Caractères simples
            if not converted:
                char = word[i]
                if char in self.vowels_oral:
                    result += self.vowels_oral[char]
                    i += 1
                    converted = True
                elif char in self.consonants:
                    result += self.consonants[char]
                    i += 1
                    converted = True
                else:
                    i += 1
        
        return result

    def _extract_core_word(self, word: str) -> str:
        """Extrait le mot lexical core pour liaison après élisions."""
        if "'" in word:
            word = word.split("'")[-1]
        if "-" in word:
            word = word.split("-")[0]
        return word.strip()
    
    def _handle_h_aspire_improved(self, word: str) -> bool:
        """Vérification améliorée pour h aspiré. Retourne True si h aspiré."""
        word = word.lower().strip()
        if not word.startswith("h"):
            return False
        return word in self.H_ASPIRE_EXTENDED or word in self.H_ASPIRE
    
    def suggest_liaison_for_word(self, word: str) -> Optional[str]:
        """Suggère la consonne de liaison pour un mot donné."""
        word_normalized = self._extract_core_word(word.lower())
        return self.LIAISON_MAP_EXTENDED.get(word_normalized)

    def link_words(self, words: list[str], analyses: list[str]) -> list[str]:
        """Applique les liaisons/enchaînements aux IPA unitaires de manière sécurisée."""
        if not words or not analyses or len(words) != len(analyses):
            return analyses

        linked = list(analyses)

        for i in range(len(words) - 1):
            current_raw = words[i]
            current = current_raw.lower().strip(".,!?;:«»\"'()[]")
            current_for_liaison = current.split("'")[-1]
            next_word = words[i + 1].lower().strip(".,!?;:«»\"'()[]")

            if not current or not next_word:
                continue

            # 1. Ignorer les mots avec trait d'union gérés séparément ou les mots interdits
            if "-" in current_raw:
                linked[i] = self._handle_hyphenated(current_raw, linked[i], next_word)
                continue

            if current in self.FORBIDDEN_LIAISON_AFTER:
                continue

            # 2. Le mot suivant doit impérativement commencer par une voyelle ou un h muet
            if not self._starts_with_vowel(next_word):
                continue

            # 3. Recherche prioritaire dans la table de liaison exacte
            consonant = self.LIAISON_MAP_EXTENDED.get(current_for_liaison)
            
            # Si non trouvé, fallback sur la table standard, sinon pas de liaison sauvage
            if not consonant:
                consonant = self.LIAISON_MAP.get(current_for_liaison)

            if consonant:
                linked[i] = self._append_linked_consonant(linked[i], consonant)

        return linked

    def _starts_with_vowel(self, word: str) -> bool:
        """Retourne True si le mot suivant commence par un son vocalique."""
        word = word.lower().strip()
        if not word:
            return False

        if self._handle_h_aspire_improved(word) or word in self.H_ASPIRE:
            return False

        if word.startswith("h"):
            return len(word) > 1 and word[1] in self.VOWELS

        # Nettoyage des accents pour la vérification de la voyelle initiale (ex: é -> e)
        normalized_first_char = word[0]
        if normalized_first_char in 'éèêëàâäîïôöùûüç':
            return True

        return normalized_first_char in self.VOWELS

    def _append_linked_consonant(self, ipa: str, consonant: str) -> str:
        """Ajoute une consonne de liaison sans casser l'IPA existante."""
        if not ipa:
            return ipa
        if ipa.endswith(consonant) or f"‿{consonant}" in ipa:
            return ipa
        return f"{ipa}‿{consonant}"

    def _handle_hyphenated(self, word: str, ipa: str, next_word: str = "") -> str:
        """Traite les formes pronominales avec trait d'union."""
        lower = word.lower()

        if "-t-" in lower:
            parts = lower.split("-t-", 1)
            if len(parts) == 2:
                left = self._safe_word_ipa(parts[0])
                right = self._safe_word_ipa(parts[1])
                if left and right:
                    return f"{left}‿t {right}"

        if lower.startswith(("dit-", "va-", "a-", "parle-", "chante-", "est-")):
            parts = lower.split("-", 1)
            if len(parts) == 2:
                left_ipa = self._safe_word_ipa(parts[0])
                right_ipa = self._safe_word_ipa(parts[1])
                if left_ipa and right_ipa:
                    return f"{left_ipa}‿t {right_ipa}"

        return ipa

    def _safe_word_ipa(self, word: str) -> str:
        """Conversion minimale utilisée uniquement pour les formes avec trait d'union."""
        simple = {
            "dit": "di", "va": "va", "a": "a", "parle": "paʁl",
            "chante": "ʃɑ̃t", "est": "ɛ", "il": "il", "elle": "ɛl",
            "ils": "il", "elles": "ɛl", "on": "ɔ̃",
        }
        return simple.get(word.lower(), "")

    def _is_liaison_word(self, word: str) -> bool:
        """Détermine si un mot peut déclencher une liaison."""
        return word in {
            "les", "des", "ces", "mes", "tes", "ses", "nos", "vos", "leurs",
            "un", "bon", "tout", "petit", "grand", "aux", "dans", "sans",
            "comment", "quand", "font"
        }

    def _get_liaison_consonant(self, word: str) -> str:
        """Associe la consonne muette finale qui reparaît lors de la liaison."""
        mapping = {
            "comment": "t",
            "les": "z", "des": "z", "ces": "z", "mes": "z", "tes": "z", "ses": "z",
            "nos": "z", "vos": "z", "leurs": "z", "aux": "z", "dans": "z", "sans": "z",
            "un": "n", "bon": "n", "en": "n", "on": "n",
            "tout": "t", "petit": "t", "grand": "t"
        }
        return mapping.get(word, "")

    def get_phonemes(self, word: str) -> List[PhoneticSegment]:
        """Décompose un mot en segments phonétiques."""
        try:
            if not word:
                return []
            
            word = word.lower().strip()
            segments = []
            ipa = self._convert_word_to_ipa(word)
            
            i_graph = 0
            i_ipa = 0
            syllable = 1
            
            while i_graph < len(word) and i_ipa < len(ipa):
                if i_graph < len(word) and word[i_graph] in 'aeiouyàâäéèêëïîôöùûüœæ':
                    syllable_num = syllable
                    syllable += 1
                else:
                    syllable_num = syllable
                
                if i_graph + 2 <= len(word) and word[i_graph:i_graph+2] in self.digraphs:
                    grapheme = word[i_graph:i_graph+2]
                    i_graph += 2
                else:
                    grapheme = word[i_graph]
                    i_graph += 1
                
                ipa_segment = ipa[i_ipa]
                i_ipa += 1
                
                segments.append(PhoneticSegment(
                    grapheme=grapheme,
                    ipa=ipa_segment,
                    syllable_number=syllable_num
                ))
            
            return segments
        except Exception as e:
            logger.error(f"Erreur extraction phonèmes: {e}")
            return []
    
    def analyze_complete(self, word: str) -> IPAAnalysis:
        """Analyse phonétique complète d'un mot."""
        try:
            if not word:
                return IPAAnalysis(original_text="", ipa_full="")
            
            word = word.lower().strip()
            ipa_full = self._convert_word_to_ipa(word)
            segments = self.get_phonemes(word)
            
            syllable_count = max([s.syllable_number for s in segments]) if segments else 1
            consonants = [s.ipa for s in segments if self._is_consonant_ipa(s.ipa)]
            vowels = [s.ipa for s in segments if self._is_vowel_ipa(s.ipa)]
            features = self._extract_features(ipa_full, segments)
            
            return IPAAnalysis(
                original_text=word,
                ipa_full=f"/{ipa_full}/",
                segments=segments,
                syllable_count=syllable_count,
                consonants=consonants,
                vowels=vowels,
                features=features
            )
        except Exception as e:
            logger.error(f"Erreur analyse complète: {e}")
            return IPAAnalysis(original_text=word, ipa_full="")
    
    def _is_consonant_ipa(self, ipa_char: str) -> bool:
        """Vérifie si c'est une consonne IPA."""
        return ipa_char in 'pbtdkgfvszʃʒθðmn ɲŋlʁjwɥxɣ'
    
    def _is_vowel_ipa(self, ipa_char: str) -> bool:
        """Vérifie si c'est une voyelle IPA."""
        return ipa_char in 'ieyøœuoɔaɑʌəɛɑ̃ɛ̃œ̃ɔ̃' or ipa_char.isalpha()
    
    def _extract_features(self, ipa_full: str, segments: List[PhoneticSegment]) -> Dict[str, str]:
        """Extrait traits phonétiques du mot."""
        features = {}
        has_nasal = any('̃' in s.ipa for s in segments)
        has_uvular = 'ʁ' in ipa_full
        
        features['nasality'] = 'nasal' if has_nasal else 'oral'
        features['has_uvular_r'] = 'yes' if has_uvular else 'no'
        features['fricatives_count'] = sum(1 for s in segments if s.ipa in 'fvszʃʒ')
        
        if segments:
            features['starts_with'] = self._classify_sound(segments[0].ipa)
            features['ends_with'] = self._classify_sound(segments[-1].ipa)
        
        return features
    
    def _classify_sound(self, ipa_sound: str) -> str:
        """Classifie un son IPA."""
        if ipa_sound in 'pbtdkgqɢ':
            return 'stop'
        elif ipa_sound in 'fvszʃʒθðxɣχ':
            return 'fricative'
        elif ipa_sound in 'mn ɲŋ':
            return 'nasal'
        elif ipa_sound in 'lʎ':
            return 'lateral'
        elif ipa_sound in 'jwɥʍ':
            return 'approximant'
        elif ipa_sound in 'ʁrɾ':
            return 'rhotic'
        elif self._is_vowel_ipa(ipa_sound):
            return 'vowel'
        return 'unknown'
    
    def compare_ipa(self, word1: str, word2: str) -> Dict[str, Union[str, float]]:
        """Compare deux mots en IPA."""
        try:
            ipa1 = self._convert_word_to_ipa(word1)
            ipa2 = self._convert_word_to_ipa(word2)
            
            matches = sum(1 for a, b in zip(ipa1, ipa2) if a == b)
            similarity = matches / max(len(ipa1), len(ipa2)) if max(len(ipa1), len(ipa2)) > 0 else 0
            
            return {
                'word1': word1,
                'ipa1': f"/{ipa1}/",
                'word2': word2,
                'ipa2': f"/{ipa2}/",
                'identical': ipa1 == ipa2,
                'similarity': round(similarity, 2),
                'distance': abs(len(ipa1) - len(ipa2))
            }
        except Exception as e:
            logger.error(f"Erreur comparaison: {e}")
            return {}


# ════════════════════════════════════════════════════════════════════════════════
# SINGLETON & FACTORY
# ════════════════════════════════════════════════════════════════════════════════

_ipa_phonetizer: Optional[FrenchIPAPhoneticizer] = None


def get_ipa_phonetizer(voice_type: VoiceType = VoiceType.NEUTRAL) -> FrenchIPAPhoneticizer:
    """Obtient instance de phonétiseur IPA (singleton)."""
    global _ipa_phonetizer
    if _ipa_phonetizer is None:
        _ipa_phonetizer = FrenchIPAPhoneticizer(voice_type=voice_type)
    return _ipa_phonetizer


def reset_ipa_phonetizer() -> None:
    """Réinitialise le singleton."""
    global _ipa_phonetizer
    _ipa_phonetizer = None
    logger.info("Phonétiseur IPA réinitialisé")


# ════════════════════════════════════════════════════════════════════════════════
# TESTS & DÉMONSTRATION
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║       Phonétiseur IPA Français - Démonstration                   ║")
    print("╚══════════════════════════════════════════════════════════════════╝\n")
    
    try:
        phonetizer = get_ipa_phonetizer()
        
        test_words = [
            "chat",
            "extraordinaire",
            "bonjour",
            "français",
            "géographie",
            "monsieur",
            "aujourd'hui",
            "whisky",
            "wifi",
            "oignon",
            "pays",
            "roi",
            "plaisir",
            "examen",
            "pain",
            "coin",
        ]
        
        print("TRANSCRIPTIONS IPA:\n")
        print("=" * 80)
        
        for word in test_words:
            try:
                ipa = phonetizer.text_to_ipa(word)
                analysis = phonetizer.analyze_complete(word)
                
                print(f"\n📝 Mot: {word}")
                print(f"   IPA: {ipa}")
                print(f"   Syllabes: {analysis.syllable_count}")
                print(f"   Voyelles: {', '.join(analysis.vowels)}")
                print(f"   Consonnes: {', '.join(analysis.consonants)}")
                print(f"   Traits: {analysis.features}")
                
            except Exception as e:
                print(f"❌ Erreur '{word}': {e}")
        
        print("\n" + "=" * 80)
        print("\n✓ Démonstration terminée avec succès!")
        print("✓ Phonétiseur IPA prêt pour intégration TTS.\n")
    
    except Exception as e:
        print(f"❌ Erreur fatale: {e}")
        logger.exception("Erreur dans les tests")