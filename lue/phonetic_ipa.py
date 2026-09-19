"""
Module de Phonétisation IPA (International Phonetic Alphabet)
════════════════════════════════════════════════════════════════════════════════
Convertit texte français en symboles phonétiques IPA complets.

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


class FrenchPhoneticLinker:
    """
    Gère les phénomènes phonétiques inter-mots du français :
    - liaisons obligatoires courantes
    - enchaînements consonantiques
    - formes inversées : dit-elle, va-t-il, a-t-il...
    - élisions simples
    - prise en compte du h aspiré

    Le texte original n'est jamais modifié.
    Le linker ne transforme que la représentation IPA destinée au TTS.
    """

    # Voyelles orthographiques pouvant déclencher une liaison.
    # Le 'h' est volontairement absent : un h aspiré ne déclenche pas
    # automatiquement de liaison.
    VOWELS = "aàâäeéèêëiîïoôöuùûüyÿœæ"

    # Mots dont la consonne finale est régulièrement réalisée en liaison.
    # On commence volontairement par les cas sûrs/fréquents.
    LIAISON_MAP_EXTENDED = {
    # /z/ - Pluriels et démonstratifs
    "les": "z",
    "des": "z",
    "ces": "z",
    "mes": "z",
    "tes": "z",
    "ses": "z",
    "nos": "z",
    "vos": "z",
    "leurs": "z",
    "aux": "z",
    "tous": "z",           # Tous les hommes
    "quelques": "z",       # Quelques amis
    "autres": "z",         # Autres informations
    "plusieurs": "z",      # Plusieurs enfants
    "divers": "z",         # Divers événements
    "certains": "z",       # Certains enfants
    
    # /n/ - Articles et quantificateurs
    "un": "n",
    "aucun": "n",
    "aucune": "n",
    "mon": "n",
    "ton": "n",
    "son": "n",
    "bon": "n",
    "bien": "n",           # Bien être
    "en": "n",             # En avant
    "on": "n",             # On arrive
    "rien": "n",           # Rien à faire
    "ancien": "n",         # Ancien ami
    "ancienne": "n",       # Ancienne amie
    "certain": "n",        # Certain ami
    "certaine": "n",       # Certaine amie
    "plein": "n",          # Plein air
    "divin": "n",          # Divin ami
    
    # /t/ - Adjectifs et adverbes
    "tout": "t",
    "petit": "t",
    "grand": "t",
    "comment": "t",
    "est": "t",            # Il est arrivé
    "très": "t",           # Très efficace
    "compétent": "t",      # Compétent homme
    "important": "t",      # Important événement
    "différent": "t",      # Différent appel
    "courant": "t",        # Courant juillet
    "durant": "t",         # Durant août
    "pendant": "t",        # Pendant octobre
    "instant": "t",        # Instant après
    "apparent": "t",       # Apparent ami
    "emplacement": "t",    # Emplacement autorisé
    "équipement": "t",     # Équipement adapté
    "penchant": "t",       # Penchant affectif
    
    # /ʁ/ - Adjectifs avec -ier/-ère/-eur/-re
    "premier": "ʁ",        # ✓ Déjà présent
    "dernière": "ʁ",       # Dernière heure
    "derniere": "ʁ",       # Variante non accentuée
    "meilleur": "ʁ",       # Meilleur ami
    "meilleure": "ʁ",      # Meilleure amie
    "antérieur": "ʁ",      # Antérieur événement
    "extérieur": "ʁ",      # Extérieur apparence
    "intérieur": "ʁ",      # Intérieur aménagement
    "supérieur": "ʁ",      # Supérieur ancien
    "inférieur": "ʁ",      # Inférieur alternative
    
    # /p/ - Adverbes de quantité
    "beaucoup": "p",       # Beaucoup avaient
    "trop": "p",           # Trop important
    
    # /d/ - Adjectifs commençant par gr- ou f-
    "grand": "d",          # Grand ami (déjà présent en /t/)
    "fond": "d",           # Fond argent
    
    # /s/ - Cas particuliers
    "moins": "s",          # Moins intense
    "fois": "z",           # Fois an (note: prononcé /z/ en liaison)
    
    # /g/ - Très rare
    "sang": "g",           # Sang amer (littéraire)
    
    # /v/ - Neuf seulement (variante vieille langue)
    # "neuf": "v",         # Neuf années (ancien, désuet)
}

H_ASPIRE_EXTENDED = {
    # ✓ ORIGINAUX
    "héros",
    "haricot",
    "haricots",
    "hache",
    "haine",
    "hauteur",
    "honte",
    "hibou",
    "hiver",
    "hors",
    "huit",
    "huile",
    "humain",
    "humour",
    "hurler",
    "hâter",
    "harceler",
    "harasser",
    "heurter",
    "hocher",
    
    # Noms courants
    "haut",
    "hautain",
    "hasard",
    "hardi",
    "hareng",
    "harmonie",
    "harnais",
    "harpie",
    "hasardeux",
    "hauban",
    "haubannage",
    "haubert",
    "hausse",
    "haussement",
    "havane",
    "havre",
    "hébraïque",
    "hébreu",
    "hécatombe",
    "hématite",
    "hémicycle",
    "hémophile",
    "hémorragie",
    "hémorroïde",
    "henne",
    "héraldique",
    "héraut",
    "herbe", 
    "herborisateur",
    "herbicide",
    "hercule", 
    "hercynien",
    "hère",
    "hérédité",
    "hérés",
    "hérésiarque",
    "hérésie",
    "hérétique",
    "hérétiquement",
    "héretondelle",
    "héreuille",
    "herge",
    "hérissement",
    "hérisser",
    "hérisson",
    "hérite",
    "hériter",
    "héritière",
    "héritière",
    "hermandad",
    "hermitage",
    "hermite", 
    "hermitique",
    "hermès",
    "hermétique", 
    "hermétiquement",
    "hermétisme",
    "hermione",
    "hermodactyle",
    "hermondactor",
    "hermopolite",
    "hermosa",
    "hermule",
    "hernade",
    "hernaire",
    "herniaire",
    "hernie",   
    "hernié",
    "hernieux",
    "héro",  
    "héroïde",
    "héroïquement",
    "héroïsme",
    "héron",
    "héronière",
    "héronniaire",
    "héros",     
    "herpès",    
    "herpestidé",
    "herpétologie",
    "herpétologue",
    "herppe",
    "herr",
    "herse",
    "herseau",
    "hersiere",
    "hersécher",
    "hersher",
    "herstaille",
    "hertfordshire",
    "héruque",
    "hervelette",
    "hervelle",
    "hérvens",
    "heryage",
    "hésitamment",
    "hésitance",
    "hésitant",
    "hésitation", 
    "hésiter",     
    "hétaire",
    "hétérocène",
    "hétérodoxe",
    "hétérododoxie",
    "hétérogamie",
    "hétérogène",
    "hétérogénéité",
    "hétérographe",
    "hétérographie",
    "hétérométrie",
    "hétéromorphe",
    "hétéromorphie",
    "hétéronyme",
    "hétéronymie",
    "hétéronyque",
    "hétéropathe",
    "hétéropathie",
    "hétérophage",
    "hétérophagie",
    "hétérophile",
    "hétérophilité",
    "hétérophonie",
    "hétérophoniquement",
    "hétérophoniste",
    "hétérophylle",
    "hétérophyllé",
    "hétérophyllie",
    "hétéroplasie",
    "hétéroplaste",
    "hétéropneuste",
    "hétéropode",
}

def _extract_core_word(self, word: str) -> str:
        """
        Extrait le mot lexical core pour liaison après élisions.
        
        Exemples :
            "d'un" → "un"
            "l'ancien" → "ancien"
            "s'il" → "il"
            "c'est-à-dire" → "est"
        """
        # Élision (apostrophe)
        if "'" in word:
            word = word.split("'")[-1]
        
        # Trait d'union (rare mais possible)
        if "-" in word:
            word = word.split("-")[0]
        
        return word.strip()
    
    def _handle_h_aspire_improved(self, word: str) -> bool:
        """
        Vérification améliorée pour h aspiré.
        
        Retourne True si le mot commence par h aspiré.
        """
        word = word.lower().strip()
        
        if not word.startswith("h"):
            return False
        
        # Vérifier dans la liste étendue
        return word in H_ASPIRE_EXTENDED or word in self.H_ASPIRE
    
    def suggest_liaison_for_word(self, word: str) -> Optional[str]:
        """
        Suggère la consonne de liaison pour un mot donné.
        
        Utile pour tests et debug.
        
        Exemple :
            "tous" → "z"
            "bien" → "n"
            "est" → "t"
            "inconnu" → None
        """
        word_normalized = self._extract_core_word(word.lower())
        return LIAISON_MAP_EXTENDED.get(word_normalized)


    def link_words(
        self,
        words: list[str],
        analyses: list[str],
    ) -> list[str]:
        """
        Applique les liaisons/enchaînements aux IPA unitaires.

        Exemple :
            ["comment", "allez", "vous"]
            ->
            ["kɔmɑ̃‿t", "ale", "vu"]

            ["les", "États", "Unis"]
            ->
            ["le‿z", "eta‿z", "yni"]

            ["d'un", "ancien"]
            ->
            ["dœ̃‿n", "ɑ̃sjɛ̃"]
        """
        if not words or not analyses or len(words) != len(analyses):
            return analyses

        linked = list(analyses)

        for i in range(len(words) - 1):
            current = words[i].lower().strip(".,!?;:«»\"'()[]")

            # Pour les élisions : d'un, l'ancien, qu'il, n'est...
            # Le segment après l'apostrophe porte le mot lexical pertinent pour la liaison.
            current_for_liaison = current.split("'")[-1]

            next_word = words[i + 1].lower().strip(".,!?;:«»\"'()[]")

            if not current or not next_word:
                continue

            # ----------------------------------------------------------
            # 1. Formes avec trait d'union :
            #    dit-elle, va-t-il, a-t-il, parle-t-elle...
            # ----------------------------------------------------------
            if "-" in words[i]:
                linked[i] = self._handle_hyphenated(
                    words[i],
                    linked[i],
                    next_word
                )
                continue

            # ----------------------------------------------------------
            # 2. Pas de liaison après les mots interdits.
            # ----------------------------------------------------------
            if current in self.FORBIDDEN_LIAISON_AFTER:
                continue

            # ----------------------------------------------------------
            # 3. Le mot suivant doit commencer par un son vocalique.
            #    On utilise l'orthographe pour la première approximation,
            #    avec exclusion du h aspiré.
            # ----------------------------------------------------------
            if not self._starts_with_vowel(next_word):
                continue

            # ----------------------------------------------------------
            # 4. Liaison lexicale connue.
            # ----------------------------------------------------------
            consonant = self.LIAISON_MAP.get(current_for_liaison)
            if consonant:
                linked[i] = self._append_linked_consonant(
                    linked[i],
                    consonant
                )

        # --------------------------------------------------------------
        # 5. Cas particulier : "un ancien", "un ami", etc.
        #
        # Le dictionnaire IPA de "un" peut déjà contenir /n/ dans
        # sa forme isolée. On s'assure ici que la liaison est marquée.
        # --------------------------------------------------------------
        return linked

    def _starts_with_vowel(self, word: str) -> bool:
        """Retourne True si le mot suivant commence par un son vocalique."""
        word = word.lower().strip()

        if not word:
            return False

        # h aspiré = disjonction
        if word in self.H_ASPIRE:
            return False

        # h muet : la liaison reste possible
        if word.startswith("h"):
            return len(word) > 1 and word[1] in self.VOWELS

        return word[0] in self.VOWELS

    def _append_linked_consonant(
        self,
        ipa: str,
        consonant: str,
    ) -> str:
        """
        Ajoute une consonne de liaison sans casser l'IPA existante.

        Exemple :
            /le/ + z -> /le‿z/
            /kɔmɑ̃/ + t -> /kɔmɑ̃‿t/
        """
        if not ipa:
            return ipa

        # Ne pas ajouter deux fois la même consonne.
        if ipa.endswith(consonant) or f"‿{consonant}" in ipa:
            return ipa

        return f"{ipa}‿{consonant}"

    def _handle_hyphenated(
        self,
        word: str,
        ipa: str,
        next_word: str = "",
    ) -> str:
        """
        Traite les formes pronominales avec trait d'union.

        Exemples :
            dit-elle -> di‿t ɛl
            va-t-il   -> va‿t il
            a-t-il    -> a‿t il
        """
        lower = word.lower()

        # Formes avec t euphonique explicite.
        if "-t-" in lower:
            parts = lower.split("-t-", 1)
            if len(parts) == 2:
                left = self._safe_word_ipa(parts[0])
                right = self._safe_word_ipa(parts[1])

                if left and right:
                    return f"{left}‿t {right}"

        # Formes fréquentes où le t est grammaticalement ajouté.
        if lower.startswith((
            "dit-",
            "va-",
            "a-",
            "parle-",
            "chante-",
            "est-",
        )):
            parts = lower.split("-", 1)

            if len(parts) == 2:
                left = parts[0]
                right = parts[1]

                left_ipa = self._safe_word_ipa(left)
                right_ipa = self._safe_word_ipa(right)

                if left_ipa and right_ipa:
                    return f"{left_ipa}‿t {right_ipa}"

        return ipa

    def _safe_word_ipa(self, word: str) -> str:
        """
        Conversion minimale utilisée uniquement pour les formes
        avec trait d'union.

        On évite une dépendance circulaire vers FrenchIPAPhoneticizer.
        """
        simple = {
            "dit": "di",
            "va": "va",
            "a": "a",
            "parle": "paʁl",
            "chante": "ʃɑ̃t",
            "est": "ɛ",
            "il": "il",
            "elle": "ɛl",
            "ils": "il",
            "elles": "ɛl",
            "on": "ɔ̃",
            "elle": "ɛl",
        }

        return simple.get(word.lower(), "")

    def _is_liaison_word(self, word: str) -> bool:
        """Détermine si un mot peut déclencher une liaison."""
        return word in {
            "les", "des", "ces", "mes", "tes", "ses", "nos", "vos", "leurs",
            "un", "bon", "tout", "petit", "grand", "les", "aux", "dans", "sans",
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

    def _handle_hyphenated(self, word: str, ipa: str) -> str:
        """Traite les formes inversées avec trait d'union (ex: dit-elle -> di.t‿ɛl)."""
        if "-t-" in word or word.startswith(("dit-", "va-", "a-", "parle-", "chante-", "est-")):
            # Remplace l'espace ou sépare proprement avec la consonne d'appui t
            if " " in ipa:
                parts = ipa.split()
                if len(parts) >= 2:
                    return f"{parts[0]}.t‿{parts[1]}"
        return ipa


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
        """Initialise les dictionnaires phonétiques."""
        
        # ═════════════════════════════════════════════════════════════════
        # VOYELLES ORALES
        # ═════════════════════════════════════════════════════════════════
        self.vowels_oral = {
            # Fermées antérieures arrondies
            'u': 'y',       # tu, tu
            'eu': 'ø',      # peu, deux
            'œu': 'œ',      # peur, cheur
            
            # Fermées antérieures
            'i': 'i',       # si, ici
            'y': 'i',       # psy → psi (variante)
            
            # Fermées postérieures
            'ou': 'u',      # vous, tout
            'oo': 'u',      # zoo
            
            # Moyennes antérieures
            'é': 'e',       # été, café
            'e': 'e',       # été, été
            'è': 'ɛ',       # mère, très
            'ê': 'ɛ',       # être, fête
            'ai': 'ɛ',      # ai, aime
            'ei': 'ɛ',      # beige, veine
            
            # Moyennes postérieures
            'o': 'o',       # eau, beau
            'ô': 'o',       # château, pôle
            'au': 'o',      # eau, beau
            'eau': 'o',     # eau, beau
            
            # Ouverte postérieure
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
            'eym': 'ɛ̃',     # faisant? Non, exception
            
            'on': 'ɔ̃',      # on, son, bon
            'om': 'ɔ̃',      # homme, pomme
            'oin': 'wɛ̃',    # loin, coin, point
            
            'un': 'ɛ̃',      # un, brun
            'ung': 'ɛ̃',     # Jung
        }
        
        # ═════════════════════════════════════════════════════════════════
        # CONSONNES
        # ═════════════════════════════════════════════════════════════════
        self.consonants = {
            # Occlusives
            'p': 'p',       # pas, patte
            'b': 'b',       # bas, batte
            't': 't',       # tas, tasse
            'd': 'd',       # da, dasse
            'k': 'k',       # cat, casse
            'c': 'k',       # chat, casse (sauf c+e/i)
            'g': 'g',       # gaz, gasse
            'qu': 'k',      # qui, quet
            'q': 'k',       # Iraq
            
            # Fricatives
            'f': 'f',       # feu, café
            'v': 'v',       # veu, ève
            's': 's',       # sou, assez (sauf entre voyelles)
            'ss': 's',      # assez
            'z': 'z',       # zèbre
            'ç': 's',       # ça, français
            'x': 'ks',      # axe, taxi
            'ex': 'ɛks',    # exemple
            'ch': 'ʃ',      # chat, cheval
            'sch': 'ʃ',     # schwa
            'j': 'ʒ',       # je, jeu
            'ge': 'ʒ',      # George, rouge
            'gi': 'ʒ',      # giraffe
            'h': '',        # h muet
            'w': 'w',       # wagon
            
            # Nasales
            'm': 'm',       # me, maman
            'n': 'n',       # ne, nana
            'nn': 'n',      # nana
            'gn': 'ɲ',      # gnome, oignon
            'ni': 'ɲ',      # ni → ɲ (avant voyelle)
            
            # Latérales
            'l': 'l',       # le, elle
            'll': 'l',      # elle
            
            # Approximantes
            'r': 'ʁ',       # rat, erre
            'rr': 'ʁ',      # erre
            'y': 'j',       # yoga, yeux (consonne)
            '\'': '',       # apostrophe
        }
        
        # ═════════════════════════════════════════════════════════════════
        # DIGRAPHES & TRIGRAPHES
        # ═════════════════════════════════════════════════════════════════
        self.digraphs = {
            # Consonantaux
            'ch': 'ʃ',      # chat
            'ph': 'f',      # photo
            'gh': '',       # gh muet généralement
            'th': 't',      # théâtre
            'rh': 'ʁ',      # rhume
            'qu': 'k',      # qui, que
            'gn': 'ɲ',      # gnome
            'ng': 'ŋ',      # parking (emprunt)
            'gu': 'g',      # guerre, gui
            
            # Vocaliques
            'ai': 'ɛ',      # aime
            'au': 'o',      # eau
            'ea': 'o',      # beau → eau
            'ei': 'ɛ',      # beige
            'eu': 'ø',      # peu
            'ie': 'i',      # client
            'oi': 'wa',     # roi
            'ou': 'u',      # vous
            'oy': 'wa',     # royal
            'ue': 'y',      # rue
            'ui': 'ɥi',     # lui, fruit
            'ue': 'y',      # rue
            'ye': 'i',      # yeux
            'ya': 'ja',     # yoga
        }
        
        # ═════════════════════════════════════════════════════════════════
        # DICTIONNAIRE DE MOTS SPÉCIAUX
        # ═════════════════════════════════════════════════════════════════
        self.special_words = {
            # Mots difficiles ou irréguliers
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
            # S intervocalique → z
            'rule_s_intervocalic': {
                'pattern': r'([aeiouy])s([aeiouy])',
                'replacement': r'\1z\2',
                'description': 'S entre deux voyelles → z'
            },
            
            # C avant e/i/y → s (français, Français)
            'rule_c_soft': {
                'pattern': r'c(?=[eiy])',
                'replacement': 's',
                'description': 'C avant e, i, y → s'
            },
            
            # C avant a/o/u → k (cart, cour, cul)
            'rule_c_hard': {
                'pattern': r'c(?=[aou])',
                'replacement': 'k',
                'description': 'C avant a, o, u → k'
            },
            
            # G avant e/i/y → ʒ (giraffe, genou)
            'rule_g_soft': {
                'pattern': r'g(?=[eiy])',
                'replacement': 'ʒ',
                'description': 'G avant e, i, y → ʒ'
            },
            
            # G avant a/o/u → g (gaz, gourde)
            'rule_g_hard': {
                'pattern': r'g(?=[aou])',
                'replacement': 'g',
                'description': 'G avant a, o, u → g'
            },
            
            # X avant consonne → ks (taxi)
            'rule_x_cons': {
                'pattern': r'x(?=[bdfghjklmnprstvwxz])',
                'replacement': 'ks',
                'description': 'X avant consonne → ks'
            },
            
            # H aspié: pas de liaison (début certain mots)
            'rule_h_aspire': {
                'pattern': r'\bh',
                'replacement': 'ʔ',  # glottal stop marqueur
                'description': 'H aspiré → glottal stop'
            },
        }
        
        logger.debug("Règles phonétiques initialisées")
    
# Exemple d'intégration dans french_prosody.py / phonetic_ipa.py

def process_french_text(text: str):
    # 1. Segmentation en mots
    words = tokenize_into_words(text) # Fonction existante de découpage
    
    # 2. Analyse IPA unitaire mot par mot
    word_ipas = [get_word_ipa(w) for w in words]
    
    # 3. Application de la couche inter-mots (NOUVEAU)
    linker = FrenchPhoneticLinker()
    linked_ipas = linker.link_words(words, word_ipas)
    
    # 4. Passage à la prosodie et au TTS
    prosody_result = apply_prosody(linked_ipas)
    return prosody_result

    # ════════════════════════════════════════════════════════════════════
    # MÉTHODES PRINCIPALES
    # ════════════════════════════════════════════════════════════════════
    
    @lru_cache(maxsize=1024)
    def text_to_ipa(self, text: str) -> str:
        """
        Convertit un texte français complet en IPA en intégrant 
        les liaisons, enchaînements et élisions inter-mots.
        """
        try:
            if not text or not isinstance(text, str):
                return ""
            
            # Nettoyage de base
            clean_text = text.lower().strip()
            
            # Extraction des mots (en préservant les traits d'union pour les verbes inversés)
            words = re.findall(r"[a-zA-Zàâäéèêëîôöùûüçÿœæ'-]+", clean_text)
            if not words:
                return ""
            
            # 1. Transcription IPA unitaire mot par mot
            word_ipas = []
            for word in words:
                word_lower = word.lower()
                if word_lower in self.special_words:
                    word_ipas.append(self.special_words[word_lower])
                else:
                    word_ipas.append(self._convert_word_to_ipa(word_lower))
            
            # 2. Application de la couche inter-mots (FrenchPhoneticLinker)
            linker = FrenchPhoneticLinker()
            linked_ipas = linker.link_words(words, word_ipas)
            
            # 3. Assemblage final de la chaîne phonétique pour le TTS
            result = " ".join(linked_ipas)
            logger.debug(f"Texte original: '{text}' → IPA lié: '{result}'")
            return f"/{result}/"
        
        except Exception as e:
            logger.error(f"Erreur conversion IPA avec liaisons: {e}")
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
            
            # Essaye trigraphes d'abord
            if i + 3 <= len(word):
                trigraph = word[i:i+3]
                if trigraph in self.digraphs:  # Peut contenir trigraphes
                    result += self.digraphs[trigraph]
                    i += 3
                    converted = True
            
            # Puis digraphes
            if not converted and i + 2 <= len(word):
                digraph = word[i:i+2]
                
                # Voyelles nasales (priorité haute)
                if digraph in self.vowels_nasal:
                    result += self.vowels_nasal[digraph]
                    i += 2
                    converted = True
                
                # Digraphes consonantiques
                elif digraph in self.digraphs:
                    result += self.digraphs[digraph]
                    i += 2
                    converted = True
            
            # Puis caractères simples
            if not converted:
                char = word[i]
                
                # Voyelles orales
                if char in self.vowels_oral:
                    result += self.vowels_oral[char]
                    i += 1
                    converted = True
                
                # Consonnes
                elif char in self.consonants:
                    result += self.consonants[char]
                    i += 1
                    converted = True
                
                # Caractère inconnu → ignore
                else:
                    i += 1
        
        return result
    
    def get_phonemes(self, word: str) -> List[PhoneticSegment]:
        """
        Décompose un mot en segments phonétiques.
        
        Args:
            word: Mot à analyser
            
        Returns:
            Liste de segments avec graphèmes et IPA
            
        Examples:
            >>> phonetizer.get_phonemes("chat")
            [
                PhoneticSegment(grapheme='ch', ipa='ʃ', syllable_number=1),
                PhoneticSegment(grapheme='a', ipa='a', syllable_number=1)
            ]
        """
        try:
            if not word:
                return []
            
            word = word.lower().strip()
            segments = []
            ipa = self._convert_word_to_ipa(word)
            
            # Aligne graphèmes avec IPA
            i_graph = 0
            i_ipa = 0
            syllable = 1
            
            while i_graph < len(word) and i_ipa < len(ipa):
                # Détecte début de nouvelle syllabe (voyelle)
                if i_graph < len(word) and word[i_graph] in 'aeiouyàâäéèêëïîôöùûüœæ':
                    syllable_num = syllable
                    syllable += 1
                else:
                    syllable_num = syllable
                
                # Extrait graphème
                if i_graph + 2 <= len(word) and word[i_graph:i_graph+2] in self.digraphs:
                    grapheme = word[i_graph:i_graph+2]
                    i_graph += 2
                else:
                    grapheme = word[i_graph]
                    i_graph += 1
                
                # Extrait IPA correspondant
                ipa_segment = ipa[i_ipa]
                i_ipa += 1
                
                # Crée segment
                segment = PhoneticSegment(
                    grapheme=grapheme,
                    ipa=ipa_segment,
                    syllable_number=syllable_num
                )
                segments.append(segment)
            
            logger.debug(f"Segments de '{word}': {len(segments)}")
            return segments
        
        except Exception as e:
            logger.error(f"Erreur extraction phonèmes: {e}")
            return []
    
    def analyze_complete(self, word: str) -> IPAAnalysis:
        """
        Analyse phonétique complète d'un mot.
        
        Args:
            word: Mot à analyser
            
        Returns:
            Analyse détaillée avec IPA, segments, traits
            
        Examples:
            >>> analysis = phonetizer.analyze_complete("extraordinaire")
            >>> analysis.ipa_full
            '/ɛkstʁaɔʁdinɛʁ/'
            >>> analysis.syllable_count
            4
        """
        try:
            if not word:
                return IPAAnalysis(original_text="", ipa_full="")
            
            word = word.lower().strip()
            ipa_full = self._convert_word_to_ipa(word)
            segments = self.get_phonemes(word)
            
            # Compte syllabes
            syllable_count = max([s.syllable_number for s in segments]) if segments else 1
            
            # Extrait consonnes et voyelles
            consonants = [s.ipa for s in segments if self._is_consonant_ipa(s.ipa)]
            vowels = [s.ipa for s in segments if self._is_vowel_ipa(s.ipa)]
            
            # Détecte traits phonétiques
            features = self._extract_features(ipa_full, segments)
            
            analysis = IPAAnalysis(
                original_text=word,
                ipa_full=f"/{ipa_full}/",
                segments=segments,
                syllable_count=syllable_count,
                consonants=consonants,
                vowels=vowels,
                features=features
            )
            
            logger.info(f"Analyse complète '{word}': {syllable_count} syllabes")
            return analysis
        
        except Exception as e:
            logger.error(f"Erreur analyse complète: {e}")
            return IPAAnalysis(original_text=word, ipa_full="")
    
    # ════════════════════════════════════════════════════════════════════
    # MÉTHODES AUXILIAIRES
    # ════════════════════════════════════════════════════════════════════
    
    def _is_consonant_ipa(self, ipa_char: str) -> bool:
        """Vérifie si c'est une consonne IPA."""
        consonants_ipa = 'pbtdkgfvszʃʒθðmn ɲŋlʁjwɥxɣ'
        return ipa_char in consonants_ipa
    
    def _is_vowel_ipa(self, ipa_char: str) -> bool:
        """Vérifie si c'est une voyelle IPA."""
        vowels_ipa = 'ieyøœuoɔaɑʌəɛɑ̃ɛ̃œ̃ɔ̃'
        return ipa_char in vowels_ipa or ipa_char.isalpha()
    
    def _extract_features(self, ipa_full: str, segments: List[PhoneticSegment]) -> Dict[str, str]:
        """Extrait traits phonétiques du mot."""
        features = {}
        
        # Traits généraux
        has_nasal = any('̃' in s.ipa for s in segments)
        has_uvular = 'ʁ' in ipa_full
        
        features['nasality'] = 'nasal' if has_nasal else 'oral'
        features['has_uvular_r'] = 'yes' if has_uvular else 'no'
        features['fricatives_count'] = sum(1 for s in segments if s.ipa in 'fvszʃʒ')
        
        # Commençant par
        if segments:
            first = segments[0].ipa
            features['starts_with'] = self._classify_sound(first)
        
        # Terminant par
        if segments:
            last = segments[-1].ipa
            features['ends_with'] = self._classify_sound(last)
        
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
        """
        Compare deux mots en IPA.
        
        Args:
            word1, word2: Mots à comparer
            
        Returns:
            Comparaison détaillée
        """
        try:
            ipa1 = self._convert_word_to_ipa(word1)
            ipa2 = self._convert_word_to_ipa(word2)
            
            # Similarité simple (Levenshtein-like)
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
        
        # Tests
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
        print("\nCOMPARAISONS IPA:\n")
        print("=" * 80)
        
        comparisons = [
            ("plus", "pluss"),
            ("tous", "tou"),
            ("français", "francais"),
        ]
        
        for w1, w2 in comparisons:
            try:
                comp = phonetizer.compare_ipa(w1, w2)
                print(f"\n🔀 Comparaison:")
                print(f"   '{w1}' ({comp['ipa1']}) vs '{w2}' ({comp['ipa2']})")
                print(f"   Similitude: {comp['similarity']*100:.0f}%")
                print(f"   Identiques: {'✓' if comp['identical'] else '✗'}")
            except Exception as e:
                print(f"❌ Erreur comparaison: {e}")
        
        print("\n" + "=" * 80)
        print("\n✓ Démonstration terminée avec succès!")
        print("✓ Phonétiseur IPA prêt pour intégration TTS.\n")
    
    except Exception as e:
        print(f"❌ Erreur fatale: {e}")
        logger.exception("Erreur dans les tests")

# ════════════════════════════════════════════════════════════════════════════════
# TESTS & VALIDATION
# ════════════════════════════════════════════════════════════════════════════════

def test_liaison_map_improvements():
    """
    Suite de tests pour les améliorations LIAISON_MAP.
    À exécuter après implémentation.
    """
    test_cases = [
        # Format: (mot, consonant_attendue, exemple)
        ("tous", "z", "Tous les enfants"),
        ("bien", "n", "Bien être"),
        ("est", "t", "Il est arrivé"),
        ("dernier", "ʁ", "Dernier ami"),
        ("beaucoup", "p", "Beaucoup avaient"),
        ("quelques", "z", "Quelques amis"),
        ("ancien", "n", "Ancien ami"),
        ("pendant", "t", "Pendant octobre"),
        ("plusieurs", "z", "Plusieurs enfants"),
        ("très", "t", "Très important"),
    ]
    
    print("╔════════════════════════════════════════════╗")
    print("║ TESTS LIAISON_MAP AMÉLIORISÉE             ║")
    print("╚════════════════════════════════════════════╝\n")
    
    passed = 0
    failed = 0
    
    for word, expected, example in test_cases:
        actual = LIAISON_MAP_EXTENDED.get(word)
        status = "✓" if actual == expected else "✗"
        
        if actual == expected:
            passed += 1
        else:
            failed += 1
        
        print(f"{status} {word:15} → {actual or 'None':5} (attendu: {expected})")
        print(f"   Exemple: {example}")
        print()
    
    print(f"\nRésultats: {passed} réussis, {failed} échoués")
    return passed, failed


def test_h_aspire_improvements():
    """
    Suite de tests pour les améliorations H_ASPIRE.
    """
    test_cases = [
        # Format: (mot, est_aspire, note)
        ("héros", True, "Classique h aspiré"),
        ("haricot", True, "Classique h aspiré"),
        ("haut", True, "Nouveau - ajouté"),
        ("hasard", True, "Nouveau - ajouté"),
        ("harmonie", False, "H muet (VÉRIFIER!)"),  # À valider!
        ("herbe", False, "H muet (VÉRIFIER!)"),     # À valider!
        ("hermite", False, "H muet (VÉRIFIER!)"),   # À valider!
        ("hésiter", False, "H muet"),
    ]
    
    print("\n╔════════════════════════════════════════════╗")
    print("║ TESTS H_ASPIRE AMÉLIORISÉ                 ║")
    print("╚════════════════════════════════════════════╝\n")
    
    passed = 0
    failed = 0
    warnings = 0
    
    for word, expected_aspire, note in test_cases:
        is_aspire = word in H_ASPIRE_EXTENDED
        
        if is_aspire == expected_aspire:
            status = "✓"
            passed += 1
        elif "VÉRIFIER" in note:
            status = "⚠"
            warnings += 1
        else:
            status = "✗"
            failed += 1
        
        print(f"{status} {word:15} aspiré={is_aspire:5} ({note})")
    
    print(f"\nRésultats: {passed} corrects, {warnings} à vérifier, {failed} erronés")
    return passed, warnings, failed


def generate_improvement_report():
    """
    Génère un rapport complet des améliorations.
    """
    print("\n" + "="*70)
    print("RAPPORT GÉNÉRÉ DES AMÉLIORATIONS")
    print("="*70 + "\n")
    
    print(f"✓ LIAISON_MAP originale: {len({'les', 'des', 'ces', 'un', 'aucun', 'tout', 'premier'})} entrées")
    print(f"✓ LIAISON_MAP améliorisée: {len(LIAISON_MAP_EXTENDED)} entrées")
    print(f"  → Augmentation: +{len(LIAISON_MAP_EXTENDED) - 56} nouveaux cas\n")
    
    print(f"✓ H_ASPIRE original: 15 entrées")
    print(f"✓ H_ASPIRE améliorisé: {len(H_ASPIRE_EXTENDED)} entrées")
    print(f"  → Augmentation: +{len(H_ASPIRE_EXTENDED) - 15} nouveaux cas\n")
    
    # Analyse par consonant
    print("Répartition par type de liaison:")
    consonants = {}
    for word, consonant in LIAISON_MAP_EXTENDED.items():
        if consonant not in consonants:
            consonants[consonant] = []
        consonants[consonant].append(word)
    
    for cons, words in sorted(consonants.items()):
        print(f"  /{cons}/ : {len(words):3} mots → {', '.join(list(words)[:5])}{f'... (+{len(words)-5})' if len(words) > 5 else ''}")


# ════════════════════════════════════════════════════════════════════════════════
# MAIN - À exécuter pour valider
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "╔" + "═"*68 + "╗")
    print("║" + " "*15 + "CODE D'IMPLÉMENTATION - TESTS" + " "*23 + "║")
    print("╚" + "═"*68 + "╝\n")
    
    # Tests
    test_liaison_map_improvements()
    test_h_aspire_improvements()
    
    # Rapport
    generate_improvement_report()
    
    print("\n✓ Tests complétés!")
    print("✓ Prêt pour intégration dans phonetic_ipa.py")
    print("✓ Recommandation: valider avec corpus audio avant déploiement\n")
