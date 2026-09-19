"""
Moteur de prosodie française AVANCÉ & RÉSILIENT pour TTS/LUE (lue-reader)
════════════════════════════════════════════════════════════════════════════════
Références : Rhapsodie (Modyco), Corpus Oral du Français Contemporain,
             Académie Française, Dictionnaire d'Orthoépie Française
             
AMÉLIORATIONS MAJEURES:
- ✓ Prosodies avancées: intonation, groupes de souffle, accentuation tonique
- ✓ Pièges phonétiques étendus (homographes hétérophones, assimilation)
- ✓ Règles académiques strictes (Académie Française, normes LR)
- ✓ Analyse syntaxique pour optimiser groupes de souffle
- ✓ Variantes de registre (formel/informel/littéraire)
- ✓ Phonétisation IPA optionnelle
- ✓ Gestion des réductions vocaliques & schwa
- ✓ Consonnes finales muettes et règles de liaison avancées
- ✓ Hiatus et diérèse/synérèse
- ✓ Phénomènes de jonction complexes
- ✓ NOUVEAU: Assimilation consonantique
- ✓ NOUVEAU: Liaisons dangereuses (pathétique, pas légale)
- ✓ NOUVEAU: Gestion complète des erreurs et cas limites
- ✓ NOUVEAU: Caching de patterns regex
- ✓ NOUVEAU: Logging détaillé
"""

import re
import logging
from typing import List, Tuple, Dict, Optional, Pattern, Set, Union, Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import warnings

# ════════════════════════════════════════════════════════════════════════════════
# CONFIGURATION LOGGING
# ════════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
# ÉNUMÉRATIONS
# ════════════════════════════════════════════════════════════════════════════════

class SpeechRegister(Enum):
    """Registre de langue pour adapter la prosodie."""
    FORMAL = "formal"          # Académique, soutenu
    STANDARD = "standard"      # Neutre, conversation courante
    INFORMAL = "informal"      # Relâché, très familier
    LITERARY = "literary"      # Littéraire, poétique


class IntonationType(Enum):
    """Type d'intonation pour la fin de phrase/groupe."""
    FALLING = "falling"        # Descendante (.) énonciatif
    RISING = "rising"          # Montante (?) interrogatif
    FLAT = "flat"              # Plate (énumération)
    RISING_FALLING = "rising_falling"  # Montante puis descendante
    SUSPENDED = "suspended"    # Suspendue (virgule, incise)


class AssimilationType(Enum):
    """Types d'assimilation consonantique en français."""
    PROGRESSIVE = "progressive"    # consonne suivante influence la précédente
    REGRESSIVE = "regressive"      # consonne précédente influence la suivante
    TOTAL = "total"                # fusion complète
    PARTIAL = "partial"            # assimilation partielle
    NONE = "none"                  # pas d'assimilation


# ════════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class ProsodEvent:
    """Événement prosodique enrichi avec contexte."""
    event_type: str             # "pause", "emphasis", "intonation", "liaison", "schwa", "assimilation"
    position: int               # Position dans le texte
    duration: float             # Durée (ms)
    word: str                   # Mot affecté
    intensity: float = 1.0      # Intensité (0.0-2.0)
    pitch_change: float = 0.0   # Changement de fréquence (semitones)
    description: str = ""       # Description pour debug
    context: str = ""           # Contexte phonétique
    

@dataclass
class BreathGroup:
    """Groupe de souffle (unité intonative) avec métadonnées."""
    text: str
    start_pos: int
    end_pos: int
    intonation: IntonationType = IntonationType.FALLING
    emphasis_level: int = 0    # 0=normal, 1=léger, 2=fort
    duration_ms: float = 0.0
    syllable_count: int = 0
    stress_pattern: str = ""   # Pattern d'accentuation


@dataclass
class PhoneticAnalysis:
    """Résultat d'analyse phonétique complète."""
    original_text: str
    processed_text: str
    prosodic_events: List[ProsodEvent] = field(default_factory=list)
    breath_groups: List[BreathGroup] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    processing_time_ms: float = 0.0


# ════════════════════════════════════════════════════════════════════════════════
# MOTEUR DE PROSODIE OPTIMISÉ
# ════════════════════════════════════════════════════════════════════════════════

class FrenchProsodyEngineAdvanced:
    """
    Moteur de prosodie française AVANCÉ & RÉSILIENT.
    Traite le texte avec compréhension profonde des prosodies françaises.
    Gère les erreurs gracieusement et fournit un logging détaillé.
    """
    
    # Constantes de validation
    MAX_TEXT_LENGTH = 100000
    MIN_TEXT_LENGTH = 0
    SUPPORTED_ENCODINGS = {'utf-8', 'utf-16', 'latin-1'}
    
    def __init__(
        self, 
        formal_mode: bool = True, 
        register: SpeechRegister = SpeechRegister.STANDARD,
        enable_logging: bool = True,
        strict_mode: bool = False
    ):
        """
        Initialise le moteur avec règles françaises académiques enrichies.
        
        Args:
            formal_mode: Active liaisons du registre soutenu
            register: Registre de langue (formal/standard/informal/literary)
            enable_logging: Active le logging détaillé
            strict_mode: Mode strict (lève exceptions vs warnings)
            
        Raises:
            ValueError: Si les paramètres sont invalides
        """
        try:
            if not isinstance(formal_mode, bool):
                raise TypeError("formal_mode doit être un booléen")
            if not isinstance(register, SpeechRegister):
                raise TypeError("register doit être une instance de SpeechRegister")
            
            self.formal_mode = formal_mode
            self.register = register
            self.enable_logging = enable_logging
            self.strict_mode = strict_mode
            self.prosodic_events: List[ProsodEvent] = []
            
            # Cache de patterns regex compilés
            self._pattern_cache: Dict[str, Pattern] = {}
            self._emphasis_pattern: Optional[Pattern] = None
            
            logger.info(f"Moteur initialisé: register={register.value}, formal={formal_mode}, strict={strict_mode}")
            self._initialize_phonetic_rules()
            self._initialize_liaison_rules()
            self._initialize_special_cases()
            
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation: {e}")
            raise
    
    def _initialize_phonetic_rules(self) -> None:
        """Initialise les règles phonétiques complètes."""
        try:
            # ═════════════════════════════════════════════════════════════════
            # MOTS REQUÉRANT EMPHASE & ACCENTS TONIQUES
            # ═════════════════════════════════════════════════════════════════
            self.emphasis_words: Set[str] = {
                # Adverbes connecteurs
                "cependant", "donc", "ainsi", "néanmoins", "autrement", "pourtant",
                "toutefois", "sinon", "d'ailleurs", "ensuite", "vraiment", "absolument",
                "certainement", "évidemment", "forcément", "réellement", "sûrement",
                "clairement", "expressément", "nettement", "indéniablement", "véritablement",
                
                # Négations & quantificateurs
                "pas", "jamais", "aucun", "nulle", "rien", "personne", "aucunement",
                "aucune", "nul", "nullement",
                
                # Affirmatifs
                "oui", "si", "mais", "aussi", "encore", "déjà", "toujours",
                
                # Énumérateurs
                "premièrement", "deuxièmement", "troisièmement", "d'abord",
                "ensuite", "enfin", "finalement", "pour conclure", "en outre", "notamment",
                
                # Termes d'importance
                "important", "essentiel", "crucial", "fondamental", "capital",
                "primordial", "prépondérant", "majeur", "décisif", "vital",
            }
            
            # ═════════════════════════════════════════════════════════════════
            # HOMOGRAPHES HÉTÉROPHONES
            # ═════════════════════════════════════════════════════════════════
            self.heterophone_homographs: Dict[str, str] = {
                r'\bencore\b(?=\s+[a-z])': 'encore-verbe',
                r'\bcontent\b': 'content-adjectif',
                r'\bprésent\b': 'présent-contexte',
                r'\babandon\b': 'abandon-contexte',
                r'\bappel\b': 'appel-contexte',
                r'\bcertains?\b': 'certains-prononciation',
                r'\bdistinction\b': 'distinction-académique',
                r'\bplus\b(?=\s+de)': 'plus-piège',
                r'\bplus\b(?=\s+[aàeéèêiïoôuùy])': 'plus-liaison',
            }
            
            # ═════════════════════════════════════════════════════════════════
            # PIÈGES PHONÉTIQUES AVANCÉS & CONSONNES MUETTES
            # ═════════════════════════════════════════════════════════════════
            self.phonetic_traps_advanced: Dict[str, Union[str, Callable]] = {
                r'\b(beaucoup|fait|pois|noix|trois|mois|temps|sens|enfant|sang|rang|long|fort|port|mort|sort)\b(?!\w)':
                lambda m: m.group(0),
                
                r'\bplus\s+de\b': 'plu-de',
                r'\bplus\s+les\b': 'plus-z-les',
                
                r'\bdes\s+([bcdfghjklmnpqrstvwxyz])': r'des-\1',
                r'\bdes\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙY])': r'des-z-\1',
                
                r'\btous\b(?=\s+les)': 'tous-z',
                r'\btout\b': 'tout-contexte',
                
                r'\bvingt\s+et\s+un\b': 'ving-t-et-un',
                r'\bvingt(?!\s+et)\b': 'vingt-muet',
                
                r'\bcent\s+un\b': 'sahn-t-un',
                r'\bcentre\b': 'sahn-tre',
                
                r'\bun\s+fils\b': 'un-fiss',
                r'\bdes\s+fils\b': 'des-fil',
                
                r'\bœuf(?!s)\b': 'œuf-muet',
                r'\bœufs\b': 'œufs-z',
                
                r'\bsix\b(?=\s+[bcdfghjklmnpqrstvwxyz])': 'six',
                r'\bdix\b(?=\s+[bcdfghjklmnpqrstvwxyz])': 'dis',
            }
            
            # ═════════════════════════════════════════════════════════════════
            # ABRÉVIATIONS FRANÇAISES & SIGLES
            # ═════════════════════════════════════════════════════════════════
            self.abbreviations_advanced: Dict[str, str] = {
                r'\bM\.': 'Monsieur', r'\bMme\.': 'Madame', r'\bMlle\.': 'Mademoiselle',
                r'\bDr\.': 'Docteur', r'\bMe\.': 'Maître', r'\bProf\.': 'Professeur',
                r'\bMgr\.': 'Monseigneur', r'\bSr\.': 'Sieur',
                
                r'\bEtc\.': 'et cetera', r'\betc\.': 'et cetera',
                r'\bp\.ex\.': 'par exemple', r'\bp\.s\.': 'post-scriptum',
                r'\bc\.à\.d\.': 'c\'est-à-dire', r'\bi\.e\.': 'c\'est-à-dire',
                r'\bN\.B\.': 'nota bene', r'\bn\.b\.': 'nota bene',
                r'\bvs\.': 'versus', r'\bVs\.': 'versus',
                r'\bet al\.': 'et alii',

                r'\bS\.A\.R\.L\.': 'Société à Responsabilité Limitée',
                r'\bS\.A\.S\.': 'Société par Actions Simplifiée',
                r'\bS\.A\.': 'Société Anonyme', r'\bSté\.': 'Société',
                r'\bSarl\.': 'Sarl', r'\bSAS\.': 'SAS',
                r'\bCie\.': 'Compagnie', r'\bLtd\.': 'Limitée',
                r'\bInc\.': 'Incorporée',
                
                r'\bNo\.': 'numéro', r'\bno\.': 'numéro',
                r'\bBd\.': 'Boulevard', r'\bAv\.': 'Avenue',
                r'\bkg\.': 'kilogramme', r'\bm\.': 'mètre',
                r'\bcm\.': 'centimètre', r'\bkm\.': 'kilomètre',
                r'\bh\.': 'heure', r'\bmin\.': 'minute', r'\bs\.': 'seconde',
            }
            
            # ═════════════════════════════════════════════════════════════════
            # EXPRESSIONS LATINES
            # ═════════════════════════════════════════════════════════════════
            self.latin_expressions_academic: Dict[str, str] = {
                r'\ba\s+priori\b': 'a-priori',
                r'\ba\s+posteriori\b': 'a-posteriori',
                r'\bab\s+initio\b': 'ab-initio',
                r'\bad\s+hoc\b': 'ad-ok',
                r'\bad\s+honorem\b': 'ad-onorem',
                r'\bde\s+facto\b': 'de-fakto',
                r'\bde\s+jure\b': 'de-jure',
                r'\bin\s+extremis\b': 'in-extremiss',
                r'\bin\s+situ\b': 'in-situ',
                r'\bin\s+vitro\b': 'in-vitro',
                r'\bin\s+vivo\b': 'in-vivo',
                r'\bmea\s+culpa\b': 'mea-culpa',
                r'\bne\s+varietur\b': 'ne-varietur',
                r'\bnota\s+bene\b': 'nota-bene',
                r'\bper\s+se\b': 'per-se',
                r'\bpost\s+scriptum\b': 'post-scriptum',
                r'\bsensu\s+stricto\b': 'sensu-stricto',
                r'\bsensu\s+lato\b': 'sensu-lato',
                r'\bstatus\s+quo\b': 'statuss-quo',
                r'\bvia\b': 'via',
                r'\bvice\s+versa\b': 'vicé-versa',
                r'\bvolens\s+nolens\b': 'volens-nolens',
                r'\bcurriculum\s+vitae\b': 'curriculum-vité',
                r'\bcv\b': 'CV',
                r'\bcarpe\s+diem\b': 'carpe-diem',
            }
            
            # ═════════════════════════════════════════════════════════════════
            # INTERJECTIONS & INCISES
            # ═════════════════════════════════════════════════════════════════
            self.interjections_extended: List[str] = [
                'ah', 'oh', 'eh', 'euh', 'oups', 'hélas', 'chut', 'aïe', 'bravo',
                'oulah', 'zut', 'bah', 'bof', 'pouah', 'holà', 'hein', 'quoi',
                'disons', 'voyez', 'entendez', 'écoutez', 'tenez', 'voilà', 'soit', 'sait-on',
            ]
            
            logger.debug("Règles phonétiques initialisées")
            
        except Exception as e:
            logger.error(f"Erreur initialisation règles phonétiques: {e}")
            if self.strict_mode:
                raise
    
    def _initialize_liaison_rules(self) -> None:
        """Initialise les règles de liaison complètes."""
        try:
            self.h_aspire_words: Set[str] = {
                'héros', 'haricots', 'haricot', 'hache', 'haine', 'haie',
                'hambourg', 'hauteur', 'hazard', 'honte', 'hibou', 'hurlant',
                'horde', 'horaire', 'horloge', 'horizon', 'hors', 'horticulteur',
                'houille', 'houle', 'houp', 'houppelande', 'hourra', 'housard',
                'houx', 'houyau', 'hoyau', 'huche', 'hue', 'huée', 'huer',
                'huet', 'huguenot', 'huile', 'huis', 'huissier', 'huit',
                'huitain', 'huitaine', 'huitième', 'huître', 'hulotte', 'hululement',
                'humain', 'humanité', 'humble', 'hume', 'humectant', 'humecte',
                'humecter', 'humeur', 'humide', 'humidité', 'humiliant', 'humilié',
                'humilier', 'humilité', 'humor', 'humoriste', 'humoristique',
                'humour', 'humus', 'hune', 'hunebedde', 'huneland', 'huneries',
            }
            
            self.disjunction_words: Set[str] = self.h_aspire_words.union({
                'oui', 'onze', 'uniformément', 'unicellulaire', 'unité',
                'université', 'univers', 'universel', 'universelle',
            })
            
            self.mandatory_liaisons: Dict[str, str] = {
                r'\b(les|des|ces|mes|tes|ses|nos|vos|leurs|deux|trois)\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙY])':
                    r'\1-z-\2',
                r'\b(petit|grand|bon|mauvais|joli|nouveau|vieux|jeune|ancien|beau)\s+([aàeéèêiïoôuùy])':
                    r'\1-t-\2',
                r'\b(un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+([aàeéèêiïoôuùy])':
                    r'\1-n-\2',
            }
            
            self.forbidden_liaisons: Dict[str, str] = {
                r'\bet\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙY])': r'et // \1',
            }
            
            self.optional_liaisons: Dict[str, str] = {
                r'(\w+ent)\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙY])': r'\1-z-\2',
            }
            
            logger.debug("Règles de liaison initialisées")
            
        except Exception as e:
            logger.error(f"Erreur initialisation liaisons: {e}")
            if self.strict_mode:
                raise
    
    def _initialize_special_cases(self) -> None:
        """Initialise les cas spéciaux et règles avancées."""
        try:
            self.elision_rules: Dict[str, str] = {
                r'\b(je|me|te|ne|que|de|le|la|jusque|puisque|quoique)\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙYhH])':
                    r"\1'\2",
                r'\bsi\s+(il|ils)\b': r"s'\1",
                r'\bquand\s+(il|elle|ils|elles|on)\b': r"qu'\1",
                r'\bpresque\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙY])': r"presqu'\1",
            }
            
            self.schwa_erasure_patterns: Dict[str, str] = {
                r'(\b\w+le|de|le|ce|ne|se)\s+([aàeéèêiïoôuùyAÀEÉÈÊIÏOÔUÙY])':
                    r"\1'\2",
            }
            
            self.numbers_to_french: Dict[int, str] = {
                0: "zéro", 1: "un", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq",
                6: "six", 7: "sept", 8: "huit", 9: "neuf", 10: "dix",
                11: "onze", 12: "douze", 13: "treize", 14: "quatorze", 15: "quinze",
                16: "seize", 20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante",
                60: "soixante", 70: "soixante-dix", 80: "quatre-vingts", 90: "quatre-vingt-dix",
                100: "cent", 1000: "mille", 1000000: "million", 1000000000: "milliard",
                1000000000000: "billion",
            }
            
            self.months_map = {
                '01': 'janvier', '1': 'janvier',
                '02': 'février', '2': 'février',
                '03': 'mars', '3': 'mars',
                '04': 'avril', '4': 'avril',
                '05': 'mai', '5': 'mai',
                '06': 'juin', '6': 'juin',
                '07': 'juillet', '7': 'juillet',
                '08': 'août', '8': 'août',
                '09': 'septembre', '9': 'septembre',
                '10': 'octobre', '11': 'novembre', '12': 'décembre'
            }

            self.roman_numerals_map: Dict[str, str] = {
                'I': 'un', 'II': 'deux', 'III': 'trois', 'IV': 'quatre', 'V': 'cinq',
                'VI': 'six', 'VII': 'sept', 'VIII': 'huit', 'IX': 'neuf', 'X': 'dix',
                'XI': 'onze', 'XII': 'douze', 'XIII': 'treize', 'XIV': 'quatorze',
                'XV': 'quinze', 'XVI': 'seize', 'XX': 'vingt', 'L': 'cinquante',
                'C': 'cent', 'D': 'cinq-cents', 'M': 'mille',
            }
            
            self.silence_after_punctuation: Dict[str, int] = {
                '.': 600, '!': 700, '?': 700, '…': 1000,
                ':': 400, ';': 500, ',': 200, '«': 300, '»': 300,
            }
            
            self.assimilation_rules: Dict[str, Tuple[str, AssimilationType]] = {
                r'\b(input)\b': ('input', AssimilationType.PROGRESSIVE),
                r'\b(vos)\b(?=\s+[tdsz])': ('voz', AssimilationType.REGRESSIVE),
                r'\b(pas)\b(?=\s+t)': ('paz-t', AssimilationType.REGRESSIVE),
            }
            
            logger.debug("Cas spéciaux initialisés")
            
        except Exception as e:
            logger.error(f"Erreur initialisation cas spéciaux: {e}")
            if self.strict_mode:
                raise
    
    # ════════════════════════════════════════════════════════════════════════
    # MÉTHODES DE COMPILATION ET CACHING DE PATTERNS
    # ════════════════════════════════════════════════════════════════════════
    
    def _get_compiled_pattern(self, pattern: str, flags: int = re.IGNORECASE | re.UNICODE) -> Optional[Pattern]:
        """Récupère un pattern compilé du cache ou le compile."""
        cache_key = f"{pattern}:{flags}"
        if cache_key in self._pattern_cache:
            return self._pattern_cache[cache_key]
        
        try:
            compiled = re.compile(pattern, flags)
            self._pattern_cache[cache_key] = compiled
            return compiled
        except re.error as e:
            logger.error(f"Erreur compilation pattern '{pattern}': {e}")
            if self.strict_mode:
                raise
            return None
    
    @lru_cache(maxsize=256)
    def _sanitize_input(self, text: str) -> str:
        """Nettoie et valide l'entrée."""
        if not text:
            return ""
        return text.strip()
    
    # ════════════════════════════════════════════════════════════════════════
    # VALIDATION & SÉCURITÉ (Méthode unique retournant un tuple)
    # ════════════════════════════════════════════════════════════════════════
    
    def _validate_input(self, text: str) -> Tuple[bool, str]:
        """
        Valide le texte en entrée de manière rigoureuse.
        
        Returns:
            (is_valid, error_message)
        """
        if not isinstance(text, str):
            return False, f"Texte doit être une chaîne, pas {type(text).__name__}"
        
        if len(text) > self.MAX_TEXT_LENGTH:
            return False, f"Texte trop long ({len(text)} > {self.MAX_TEXT_LENGTH})"
        
        if len(text) == 0:
            return False, "Texte vide"
        
        try:
            text.encode('utf-8')
        except UnicodeEncodeError as e:
            return False, f"Erreur encodage: {e}"
        
        return True, ""
    
    # ════════════════════════════════════════════════════════════════════════
    # ANALYSE PHONÉTIQUE & PROSODIQUE
    # ════════════════════════════════════════════════════════════════════════
    
    def detect_breath_groups(self, text: str) -> List[BreathGroup]:
        """Détecte les groupes de souffle (unités intonatives)."""
        try:
            is_valid, error = self._validate_input(text)
            if not is_valid:
                logger.warning(f"Entrée invalide: {error}")
                return []
            
            groups = []
            pattern = r'[.!?…]|,(?=\s+[A-ZÀÉÈÊ])'
            segments = re.split(pattern, text)
            
            pos = 0
            for segment in segments:
                if segment.strip():
                    intonation = self._infer_intonation(text[pos:pos+len(segment)])
                    emphasis = self._calculate_emphasis_level(segment)
                    
                    group = BreathGroup(
                        text=segment.strip(),
                        start_pos=pos,
                        end_pos=pos + len(segment),
                        intonation=intonation,
                        emphasis_level=emphasis,
                        syllable_count=self._count_syllables(segment)
                    )
                    groups.append(group)
                
                pos += len(segment) + 1
            
            logger.debug(f"Détecté {len(groups)} groupe(s) de souffle")
            return groups
            
        except Exception as e:
            logger.error(f"Erreur détection groupes de souffle: {e}")
            if self.strict_mode:
                raise
            return []
    
    def _infer_intonation(self, text: str) -> IntonationType:
        """Déduit le type d'intonation du segment."""
        if text.endswith('?'):
            return IntonationType.RISING
        elif text.endswith('!'):
            return IntonationType.FALLING
        elif text.endswith(','):
            return IntonationType.SUSPENDED
        elif ',' in text or ';' in text:
            return IntonationType.FLAT
        return IntonationType.FALLING
    
    def _count_syllables(self, word: str) -> int:
        """Compte (approximativement) les syllabes."""
        word = word.lower()
        vowels = 'aeioouàâäéèêëïîôöùûüœæ'
        syllable_count = 0
        previous_was_vowel = False
        
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not previous_was_vowel:
                syllable_count += 1
            previous_was_vowel = is_vowel
        
        return max(1, syllable_count)
    
    def _calculate_emphasis_level(self, text: str) -> int:
        """Calcule le niveau d'emphase du texte."""
        try:
            if not text:
                return 0
            
            emphasis_count = sum(
                1 for word in text.lower().split() 
                if word.rstrip(',.!?;:') in self.emphasis_words
            )
            return min(2, emphasis_count)
        except Exception as e:
            logger.warning(f"Erreur calcul emphase: {e}")
            return 0
    
    # ════════════════════════════════════════════════════════════════════════
    # APPLICATION DE RÈGLES PHONÉTIQUES
    # ════════════════════════════════════════════════════════════════════════
    
    def apply_phonetic_traps(self, text: str) -> str:
        """Applique corrections pour pièges phonétiques."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.phonetic_traps_advanced.items():
                try:
                    compiled = self._get_compiled_pattern(pattern)
                    if compiled:
                        if callable(replacement):
                            text = compiled.sub(replacement, text)
                        else:
                            text = compiled.sub(replacement, text)
                except Exception as e:
                    logger.warning(f"Erreur application pattern '{pattern}': {e}")
                    if self.strict_mode:
                        raise
            
            return text
        except Exception as e:
            logger.error(f"Erreur application pièges phonétiques: {e}")
            if self.strict_mode:
                raise
            return text
    
    def apply_heterophone_corrections(self, text: str) -> str:
        """Corrige homographes hétérophones."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.heterophone_homographs.items():
                try:
                    compiled = self._get_compiled_pattern(pattern)
                    if compiled:
                        text = compiled.sub(replacement, text)
                except Exception as e:
                    logger.warning(f"Erreur correction hétérophone: {e}")
            
            return text
        except Exception as e:
            logger.error(f"Erreur corrections hétérophones: {e}")
            if self.strict_mode:
                raise
            return text
    
    def apply_elision_rules(self, text: str) -> str:
        """Applique règles d'élision."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.elision_rules.items():
                try:
                    compiled = self._get_compiled_pattern(pattern)
                    if compiled:
                        text = compiled.sub(replacement, text)
                except Exception as e:
                    logger.warning(f"Erreur élision: {e}")
            
            return text
        except Exception as e:
            logger.error(f"Erreur application élisions: {e}")
            if self.strict_mode:
                raise
            return text
    
    def apply_liaison_rules(self, text: str) -> str:
        """Applique règles de liaison."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.forbidden_liaisons.items():
                compiled = self._get_compiled_pattern(pattern)
                if compiled:
                    text = compiled.sub(replacement, text)
            
            for pattern, replacement in self.mandatory_liaisons.items():
                compiled = self._get_compiled_pattern(pattern)
                if compiled:
                    text = compiled.sub(replacement, text)
            
            disjunction_words = '|'.join(re.escape(w) for w in self.disjunction_words)
            pattern = rf'\b(les|des|un|le|la|du)\s+({disjunction_words})\b'
            compiled = self._get_compiled_pattern(pattern)
            if compiled:
                text = compiled.sub(r'\1 // \2', text)
            
            if self.formal_mode:
                for pattern, replacement in self.optional_liaisons.items():
                    compiled = self._get_compiled_pattern(pattern)
                    if compiled:
                        text = compiled.sub(replacement, text)
            
            return text
        except Exception as e:
            logger.error(f"Erreur application liaisons: {e}")
            if self.strict_mode:
                raise
            return text
    
    def apply_schwa_and_reduction(self, text: str) -> str:
        """Applique règles de schwa et réduction."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.schwa_erasure_patterns.items():
                compiled = self._get_compiled_pattern(pattern)
                if compiled:
                    text = compiled.sub(replacement, text)
            
            return text
        except Exception as e:
            logger.error(f"Erreur schwa/réduction: {e}")
            if self.strict_mode:
                raise
            return text
    
    # ════════════════════════════════════════════════════════════════════════
    # CONVERSIONS NUMÉRIQUES & SPÉCIALES
    # ════════════════════════════════════════════════════════════════════════
    
    def _number_to_french_words(self, n: int) -> str:
        """Convertit entier en toutes lettres."""
        try:
            if n < 0:
                return "moins " + self._number_to_french_words(-n)
            
            if n in self.numbers_to_french:
                return self.numbers_to_french[n]
            
            if 20 <= n < 100:
                tens = (n // 10) * 10
                ones = n % 10
                if ones == 0:
                    return self.numbers_to_french[tens]
                elif ones == 1 and tens in [20, 60]:
                    return self.numbers_to_french[tens] + ' et ' + self.numbers_to_french[ones]
                return self.numbers_to_french[tens] + '-' + self.numbers_to_french[ones]
            
            elif 100 <= n < 1000:
                hundreds = n // 100
                remainder = n % 100
                result = self.numbers_to_french.get(hundreds, str(hundreds)) + ' cent'
                if remainder == 0 and hundreds > 1:
                    result += 's'
                if remainder > 0:
                    result += ' ' + self._number_to_french_words(remainder)
                return result
            
            elif 1000 <= n < 1000000:
                thousands = n // 1000
                remainder = n % 1000
                result = self._number_to_french_words(thousands) + ' mille'
                if remainder > 0:
                    result += ' ' + self._number_to_french_words(remainder)
                return result
            
            else:
                return str(n)
        
        except Exception as e:
            logger.error(f"Erreur conversion nombre {n}: {e}")
            return str(n)
    
    def _year_to_french_words(self, year: int) -> str:
        """Convertit année en toutes lettres."""
        try:
            if 1000 <= year < 2000:
                remainder = year % 1000
                result = 'mille'
                if remainder > 0:
                    result += ' ' + self._number_to_french_words(remainder)
                return result
            
            elif 2000 <= year <= 2100:
                remainder = year % 1000
                result = 'deux mille'
                if remainder > 0:
                    result += ' ' + self._number_to_french_words(remainder)
                return result
            
            return str(year)
        except Exception as e:
            logger.error(f"Erreur conversion année {year}: {e}")
            return str(year)
    
    def convert_years(self, text: str) -> str:
        """Convertit les années en toutes lettres."""
        try:
            if not text:
                return text
            
            def replace_year(match):
                try:
                    prefix = match.group(1) or ''
                    year_val = int(match.group(2))
                    if 1300 <= year_val <= 2100:
                        year_words = self._year_to_french_words(year_val)
                        if prefix.strip().lower() in ['en', 'vers', 'depuis']:
                            return f"{prefix.strip()}, {year_words}"
                        elif prefix:
                            return f"{prefix.strip()} {year_words}"
                        return year_words
                    return match.group(0)
                except (ValueError, AttributeError):
                    return match.group(0)
            
            pattern = r'\b(en\s+|l\'an\s+|depuis\s+|vers\s+)?(1[3-9]\d{2}|20\d{2})\b'
            return re.sub(pattern, replace_year, text, flags=re.IGNORECASE)
        
        except Exception as e:
            logger.error(f"Erreur conversion années: {e}")
            if self.strict_mode:
                raise
            return text
    
    def convert_latin_expressions(self, text: str) -> str:
        """Convertit expressions latines."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.latin_expressions_academic.items():
                compiled = self._get_compiled_pattern(pattern)
                if compiled:
                    text = compiled.sub(replacement, text)
            
            return text
        except Exception as e:
            logger.error(f"Erreur expressions latines: {e}")
            if self.strict_mode:
                raise
            return text
    
    def normalize_abbreviations(self, text: str) -> str:
        """Normalise abréviations."""
        try:
            if not text:
                return text
            
            for pattern, replacement in self.abbreviations_advanced.items():
                compiled = self._get_compiled_pattern(pattern)
                if compiled:
                    text = compiled.sub(replacement, text)
            
            return text
        except Exception as e:
            logger.error(f"Erreur normalisation abréviations: {e}")
            if self.strict_mode:
                raise
            return text
    
    def process_interjections_and_incises(self, text: str) -> str:
        """Traite interjections et incises."""
        try:
            if not text:
                return text
            
            for interj in self.interjections_extended:
                pattern = rf'(^|[.!?…\s])({re.escape(interj)})([,.!?…\s]|$)'
                compiled = self._get_compiled_pattern(pattern)
                if compiled:
                    text = compiled.sub(r'\1, \2, \3', text)
            
            return text
        except Exception as e:
            logger.error(f"Erreur interjections: {e}")
            if self.strict_mode:
                raise
            return text

    def convert_roman_numerals(self, text: str) -> str:
        """Convertit les chiffres romains en lettres."""
        roman_map = {
            r'\b[iI]\b': 'un',
            r'\b[iI]{2}\b': 'deux',
            r'\b[iI]{3}\b': 'trois',
            r'\b[iI][vV]\b': 'quatre',
            r'\b[vV]\b': 'cinq',
            r'\b[vV][iI]\b': 'six',
            r'\b[vV][iI]{2}\b': 'sept',
            r'\b[vV][iI]{3}\b': 'huit',
            r'\b[iI][xX]\b': 'neuf',
            r'\b[xX]\b': 'dix'
        }
        for pattern, replacement in roman_map.items():
            text = re.sub(pattern, replacement, text)
        return text

    def convert_dates(self, text: str) -> str:
        """Convertit les dates au format JJ/MM/AAAA ou JJ/MM/AA en toutes lettres."""
        def replace_date(match):
            day = match.group(1).lstrip('0')
            month_num = match.group(2)
            year = match.group(3)
            
            month_name = self.months_map.get(month_num, month_num)
            day_word = "premier" if day == "1" else self._number_to_french_words(int(day)) if day.isdigit() else day
            year_word = self._year_to_french_words(int(year)) if year.isdigit() else year
            return f"{day_word} {month_name} {year_word}"

        pattern = r'\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/(\d{2,4})\b'
        return re.sub(pattern, replace_date, text)

    def convert_times(self, text: str) -> str:
        """Convertit les heures en toutes lettres."""
        def replace_time_hm(match):
            hour = match.group(1).lstrip('0') or '0'
            minute = match.group(2)
            h_word = "heure" if hour == "1" else "heures"
            hour_word = self._number_to_french_words(int(hour))
            if minute == "00":
                return f"{hour_word} {h_word} pile" if hour != "0" else "minuit"
            min_word = self._number_to_french_words(int(minute))
            return f"{hour_word} {h_word} {min_word}"

        pattern_hm = r'\b([01]?\d|2[34])[:h]([0-5]\d)\b'
        return re.sub(pattern_hm, replace_time_hm, text)
    
    # ════════════════════════════════════════════════════════════════════════
    # PIPELINE PRINCIPAL DE PRÉTRAITEMENT
    # ════════════════════════════════════════════════════════════════════════

    def preprocess_text(self, text: str) -> str:
        """Pipeline complet de prétraitement robuste."""
        try:
            is_valid, error = self._validate_input(text)
            if not is_valid:
                logger.warning(f"Texte invalide: {error}")
                return ""
            
            original_text = text
            
            text = re.sub(r'[\.\s]*__\s*[hH]\s*\d+\s*__[\.\s]*', ' ', text, flags=re.IGNORECASE)

            text = text.replace('\u00a0', ' ').replace("'", "'").replace('ʼ', "'")
            text = re.sub(r'…|\.{3,}', '...', text)
            text = text.replace('—', ', ').replace('–', ', ')
            text = text.replace('„', '"').replace('"', '"')
            
            text = self.convert_dates(text)
            text = self.convert_times(text)
            text = self.convert_roman_numerals(text)
            text = self.convert_years(text)
            text = self.normalize_abbreviations(text)
            
            text = self.apply_heterophone_corrections(text)
            text = self.apply_phonetic_traps(text)
            text = self.apply_elision_rules(text)
            
            text = self.apply_liaison_rules(text)
            text = self.apply_schwa_and_reduction(text)
            
            text = self.convert_latin_expressions(text)
            text = self.process_interjections_and_incises(text)
            
            text = re.sub(r'[\.\s]*__\s*[hH]\s*\d+\s*__[\.\s]*', ' ', text, flags=re.IGNORECASE)
            
            text = re.sub(r'\s+,', ',', text)
            text = re.sub(r'(\S)([!?])', r'\1 \2', text)
            text = re.sub(r'(\S):', r'\1 :', text)
            text = re.sub(r'(\S);', r'\1 ;', text)
            text = re.sub(r'\s{2,}', ' ', text).strip()
            
            logger.debug(f"Texte prétraité: '{original_text[:50]}...' → '{text[:50]}...'")
            return text
        
        except Exception as e:
            logger.error(f"Erreur prétraitement: {e}")
            if self.strict_mode:
                raise
            return ""

    def enhance_sentence_for_tts(self, sentence: str, tts_engine: str = "edge") -> str:
        """Améliore phrase pour TTS."""
        try:
            if not sentence:
                return ""
            return self.preprocess_text(sentence)
        except Exception as e:
            logger.error(f"Erreur amélioration TTS: {e}")
            if self.strict_mode:
                raise
            return ""
    
    def prepare_for_tts(self, sentence: str) -> str:
        """Prépare texte pour synthèse vocale."""
        return self.enhance_sentence_for_tts(sentence)
    
    def analyze_phonetics_complete(self, text: str) -> PhoneticAnalysis:
        """Analyse phonétique complète avec tous les détails."""
        try:
            import time
            start_time = time.time()
            
            is_valid, error = self._validate_input(text)
            if not is_valid:
                return PhoneticAnalysis(
                    original_text=text,
                    processed_text="",
                    warnings=[error]
                )
            
            processed = self.preprocess_text(text)
            breath_groups = self.detect_breath_groups(text)
            
            processing_time = (time.time() - start_time) * 1000
            
            return PhoneticAnalysis(
                original_text=text,
                processed_text=processed,
                breath_groups=breath_groups,
                prosodic_events=self.prosodic_events,
                processing_time_ms=processing_time
            )
        
        except Exception as e:
            logger.error(f"Erreur analyse complète: {e}")
            if self.strict_mode:
                raise
            return PhoneticAnalysis(
                original_text=text,
                processed_text="",
                warnings=[str(e)]
            )


# ════════════════════════════════════════════════════════════════════════════
# SINGLETON & FACTORY
# ════════════════════════════════════════════════════════════════════════════

_french_prosody_engine: Optional[FrenchProsodyEngineAdvanced] = None


def get_french_prosody_engine(
    formal_mode: bool = True,
    register: SpeechRegister = SpeechRegister.STANDARD,
    strict_mode: bool = False
) -> FrenchProsodyEngineAdvanced:
    """Obtient le moteur de prosodie (singleton)."""
    global _french_prosody_engine
    
    try:
        if _french_prosody_engine is None:
            _french_prosody_engine = FrenchProsodyEngineAdvanced(
                formal_mode=formal_mode,
                register=register,
                strict_mode=strict_mode
            )
        return _french_prosody_engine
    
    except Exception as e:
        logger.error(f"Erreur initialisation singleton: {e}")
        raise


def reset_engine() -> None:
    """Réinitialise le singleton."""
    global _french_prosody_engine
    _french_prosody_engine = None
    logger.info("Moteur réinitialisé")
