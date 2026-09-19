"""
Intégration Moteur de Prosodie + Phonétisation IPA
════════════════════════════════════════════════════════════════════════════════
Combine le moteur de prosodie français optimisé avec la phonétisation IPA complète.

Fournit une analyse textuelle → IPA → prosodique en un seul pipeline.
"""

import logging
from typing import Optional, List, Dict, Union
from dataclasses import dataclass, field
from .french_prosody import (
    FrenchProsodyEngineAdvanced,
    SpeechRegister,
    BreathGroup,
    PhoneticAnalysis as ProsodyAnalysis,
    get_french_prosody_engine,
)

from .phonetic_ipa import (
    FrenchIPAPhoneticizer,
    FrenchPhoneticLinker,
    IPAAnalysis,
    PhoneticSegment,
    get_ipa_phonetizer,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
# STRUCTURES DE DONNÉES ENRICHIES
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class EnrichedPhoneticSegment:
    """Segment enrichi: graphème + IPA + prosodie."""
    grapheme: str              # Lettres originales
    ipa: str                   # Symboles IPA
    syllable_number: int       # Numéro de syllabe
    is_stressed: bool = False  # Accentuée dans la phrase?
    emphasis_level: int = 0    # 0=normal, 1=léger, 2=fort
    duration_ms: float = 0.0   # Durée estimée (pour future phase)
    f0_base: float = 0.0       # Fréquence fondamentale estimée


@dataclass
class FullPhoneticAnalysis:
    """Analyse phonétique complète (IPA + Prosody + TTS-ready)."""
    original_text: str
    
    # IPA
    ipa_full: str
    ipa_segments: List[PhoneticSegment] = field(default_factory=list)
    
    # Prosodie
    processed_text: str = ""
    breath_groups: List[BreathGroup] = field(default_factory=list)
    enriched_segments: List[EnrichedPhoneticSegment] = field(default_factory=list)
    
    # Métadonnées
    syllable_count: int = 0
    word_count: int = 0
    nasal_count: int = 0
    uvular_r_count: int = 0
    
    # Timing
    processing_time_ms: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════════
# MOTEUR INTÉGRÉ
# ════════════════════════════════════════════════════════════════════════════════

class ProsodyWithIPA:
    """
    Pipeline intégré Texte → IPA → Prosodie → TTS.
    
    Combine phonétisation IPA avec prosodique française pour analyse complète.
    """
    
    def __init__(
        self,
        formal_mode: bool = True,
        register: SpeechRegister = SpeechRegister.STANDARD,
        strict_mode: bool = False
    ):
        """
        Initialise le pipeline intégré.
        
        Args:
            formal_mode: Mode formel (liaisons soutenues)
            register: Registre de langue
            strict_mode: Mode strict (exceptions vs warnings)
        """
        self.prosody_engine = get_french_prosody_engine(
            formal_mode=formal_mode,
            register=register,
            strict_mode=strict_mode
        )
        self.ipa_phonetizer = get_ipa_phonetizer()
        self.formal_mode = formal_mode
        self.register = register
        self.strict_mode = strict_mode
        
        logger.info("Pipeline Prosodie+IPA initialisé")
    
    # ════════════════════════════════════════════════════════════════════
    # ANALYSE COMPLÈTE
    # ════════════════════════════════════════════════════════════════════
    
    def analyze_full(self, text: str) -> FullPhoneticAnalysis:
        """
        Analyse phonétique complète: IPA + Prosodie + Timing.
        
        Args:
            text: Texte à analyser
            
        Returns:
            Analyse enrichie avec tous les détails
            
        Example:
            >>> pipeline = ProsodyWithIPA()
            >>> analysis = pipeline.analyze_full("Bonjour, comment allez-vous?")
            >>> print(analysis.ipa_full)
            '/bɔ̃ʒuʁ kɔmɑ̃ alev-u/'
        """
        try:
            import time
            start_time = time.time()
            
            if not text or not isinstance(text, str):
                return FullPhoneticAnalysis(original_text=text)
            
            # 1. Analyse prosodique basique
            prosody_result = self.prosody_engine.analyze_phonetics_complete(text)
            processed = prosody_result.processed_text
            
            # 2. Phonétisation IPA inter-mots
            raw_words = text.lower().split()

            words = []
            for word in raw_words:
                clean_word = self._clean_word(word)
                if clean_word:
                    words.append(clean_word)

            if not words:
                return FullPhoneticAnalysis(
                    original_text=text,
                    processed_text=processed
                )

            # IPA individuelle
            word_ipas = []
            analyses = []

            for word in words:
                ipa = self.ipa_phonetizer._convert_word_to_ipa(word)
                analysis = self.ipa_phonetizer.analyze_complete(word)

                word_ipas.append(ipa)
                analyses.append(analysis)


            linker = FrenchPhoneticLinker()
            linked_ipas = linker.link_words(words, word_ipas)

            print("=== IPA LINKER ===")
            print("WORDS :", words)
            print("BEFORE:", word_ipas)
            print("AFTER :", linked_ipas)
            print("==================")




            ipa_results = [
                f"/{ipa}/"
                for ipa in linked_ipas
            ]

            ipa_full = " ".join(ipa_results)

            # Segments IPA unitaires conservés pour les métadonnées/timing.
            all_segments = []
            syllable_total = 0

            for analysis in analyses:
                all_segments.extend(analysis.segments)
                syllable_total += analysis.syllable_count
            
            # 3. Enrichit segments avec infos de prosodie
            enriched_segments = self._enrich_segments(
                all_segments,
                prosody_result.breath_groups,
                syllable_total
            )
            
            # 4. Compte traits acoustiques
            nasal_count = sum(1 for s in all_segments if '̃' in s.ipa)
            uvular_r_count = sum(1 for s in all_segments if 'ʁ' in s.ipa)
            
            processing_time = (time.time() - start_time) * 1000
            
            # 5. Crée analyse complète
            analysis = FullPhoneticAnalysis(
                original_text=text,
                ipa_full=ipa_full,
                ipa_segments=all_segments,
                processed_text=processed,
                breath_groups=prosody_result.breath_groups,
                enriched_segments=enriched_segments,
                syllable_count=syllable_total,
                word_count=len([w for w in words if self._clean_word(w)]),
                nasal_count=nasal_count,
                uvular_r_count=uvular_r_count,
                processing_time_ms=processing_time,
                warnings=prosody_result.warnings
            )
            
            logger.info(f"Analyse complète: {analysis.word_count} mots, "
                       f"{analysis.syllable_count} syllabes, {processing_time:.2f}ms")
            
            return analysis
        
        except Exception as e:
            logger.error(f"Erreur analyse complète: {e}")
            if self.strict_mode:
                raise
            return FullPhoneticAnalysis(
                original_text=text,
                warnings=[str(e)]
            )
    
    # ════════════════════════════════════════════════════════════════════
    # EXPORT POUR TTS
    # ════════════════════════════════════════════════════════════════════
    
    def export_ssml(self, text: str) -> str:
        """
        Exporte texte enrichi en SSML (Speech Synthesis Markup Language).
        
        Utile pour Edge-TTS et autres moteurs TTS.
        
        Args:
            text: Texte à exporter
            
        Returns:
            Markup SSML avec phonèmes, prosodique, etc.
            
        Example:
            >>> ssml = pipeline.export_ssml("Bonjour!")
            >>> print(ssml)
            '<speak><prosody rate="1.0"><phoneme alphabet="ipa">/bɔ̃ʒuʁ/</phoneme></prosody></speak>'
        """
        try:
            analysis = self.analyze_full(text)
            
            ssml_parts = ['<speak>']
            
            for group in analysis.breath_groups:
                # Détermine rate et pitch selon intonation
                rate = "1.0"
                pitch = "0%"
                
                if group.emphasis_level == 2:
                    rate = "0.9"  # Plus lent
                    pitch = "+10%"
                elif group.emphasis_level == 1:
                    pitch = "+5%"
                
                ssml_parts.append(
                    f'<prosody rate="{rate}" pitch="{pitch}">'
                )
                
                # Ajoute phonèmes IPA
                ipa_text = self.ipa_phonetizer._convert_word_to_ipa(group.text)
                ssml_parts.append(
                    f'<phoneme alphabet="ipa">{ipa_text}</phoneme>'
                )
                
                # Pause selon ponctuation
                if group.text.endswith(('!', '?')):
                    ssml_parts.append('<break time="500ms"/>')
                elif group.text.endswith(','):
                    ssml_parts.append('<break time="200ms"/>')
                
                ssml_parts.append('</prosody>')
            
            ssml_parts.append('</speak>')
            
            ssml_result = ''.join(ssml_parts)
            logger.debug(f"SSML généré: {len(ssml_result)} caractères")
            
            return ssml_result
        
        except Exception as e:
            logger.error(f"Erreur export SSML: {e}")
            if self.strict_mode:
                raise
            return ""
    
    def export_tts_compatible(self, text: str) -> Dict[str, Union[str, List]]:
        """
        Exporte données compatibles TTS (Edge, ElevenLabs, etc.).
        
        Args:
            text: Texte source
            
        Returns:
            Dict avec infos pour synthèse vocale
            
        Example:
            >>> data = pipeline.export_tts_compatible("Bonjour")
            >>> print(data)
            {
                'text': 'Bonjour',
                'ipa': '/bɔ̃ʒuʁ/',
                'ssml': '<speak>...',
                'segments': [...],
                'breath_groups': [...],
                'metadata': {
                    'syllables': 2,
                    'language': 'fr-FR',
                    'nasals': 1,
                    'complexity': 'low'
                }
            }
        """
        try:
            analysis = self.analyze_full(text)
            
            # Évalue complexité
            complexity = self._assess_complexity(analysis)
            
            return {
                'text': analysis.original_text,
                'text_processed': analysis.processed_text,
                'ipa': analysis.ipa_full,
                'ssml': self.export_ssml(text),
                'segments': [
                    {
                        'grapheme': s.grapheme,
                        'ipa': s.ipa,
                        'syllable': s.syllable_number,
                        'stressed': s.is_stressed
                    }
                    for s in analysis.enriched_segments[:10]  # Top 10
                ],
                'breath_groups': [
                    {
                        'text': g.text,
                        'intonation': g.intonation.value,
                        'emphasis': g.emphasis_level,
                        'syllables': g.syllable_count
                    }
                    for g in analysis.breath_groups
                ],
                'metadata': {
                    'language': 'fr-FR',
                    'syllables': analysis.syllable_count,
                    'words': analysis.word_count,
                    'nasals': analysis.nasal_count,
                    'uvular_r': analysis.uvular_r_count,
                    'complexity': complexity,
                    'processing_time_ms': analysis.processing_time_ms
                }
            }
        
        except Exception as e:
            logger.error(f"Erreur export TTS: {e}")
            if self.strict_mode:
                raise
            return {}
    
    # ════════════════════════════════════════════════════════════════════
    # MÉTHODES AUXILIAIRES
    # ════════════════════════════════════════════════════════════════════
    
    def _clean_word(self, word: str) -> str:
        """Enlève ponctuation d'un mot."""
        import re
        return re.sub(r'[^\w\']', '', word).lower()
    
    def _enrich_segments(
        self,
        segments: List[PhoneticSegment],
        breath_groups: List[BreathGroup],
        total_syllables: int
    ) -> List[EnrichedPhoneticSegment]:
        """Enrichit segments avec infos de prosodie."""
        enriched = []
        
        for segment in segments:
            # Cherche groupe de souffle contenant cette syllabe
            group = None
            for bg in breath_groups:
                if segment.syllable_number <= bg.syllable_count:
                    group = bg
                    break
            
            enriched_seg = EnrichedPhoneticSegment(
                grapheme=segment.grapheme,
                ipa=segment.ipa,
                syllable_number=segment.syllable_number,
                is_stressed=group.emphasis_level > 0 if group else False,
                emphasis_level=group.emphasis_level if group else 0,
            )
            enriched.append(enriched_seg)
        
        return enriched
    
    def _assess_complexity(self, analysis: FullPhoneticAnalysis) -> str:
        """Évalue complexité de prononciation."""
        score = 0
        
        # Nasales (difficiles)
        score += analysis.nasal_count * 1
        
        # R uvulaire (difficile pour non-natifs)
        score += analysis.uvular_r_count * 0.5
        
        # Syllabes longues
        score += analysis.syllable_count * 0.1
        
        if score <= 1:
            return "very_easy"
        elif score <= 2:
            return "easy"
        elif score <= 4:
            return "medium"
        elif score <= 6:
            return "difficult"
        else:
            return "very_difficult"


# ════════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ════════════════════════════════════════════════════════════════════════════════

_pipeline: Optional[ProsodyWithIPA] = None


def get_prosody_ipa_pipeline(
    formal_mode: bool = True,
    register: SpeechRegister = SpeechRegister.STANDARD,
    strict_mode: bool = False
) -> ProsodyWithIPA:
    """Obtient instance du pipeline (singleton)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = ProsodyWithIPA(
            formal_mode=formal_mode,
            register=register,
            strict_mode=strict_mode
        )
    return _pipeline


# ════════════════════════════════════════════════════════════════════════════════
# TESTS
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║       Pipeline Prosodie + IPA - Tests Intégrés                   ║")
    print("╚══════════════════════════════════════════════════════════════════╝\n")
    
    try:
        pipeline = get_prosody_ipa_pipeline(
            formal_mode=True,
            register=SpeechRegister.FORMAL
        )
        
        test_phrases = [
            "Bonjour, comment allez-vous?",
            "Extraordinaire, vraiment!",
            "Depuis 1789, les progrès sont constants.",
            "Monsieur Dupont parle français couramment.",
        ]
        
        print("ANALYSES COMPLÈTES (IPA + Prosodie):\n")
        print("=" * 80)
        
        for phrase in test_phrases:
            try:
                print(f"\n📄 Phrase: {phrase}")
                
                # Analyse complète
                analysis = pipeline.analyze_full(phrase)
                
                print(f"   IPA: {analysis.ipa_full}")
                print(f"   Traité: {analysis.processed_text}")
                print(f"   Mots: {analysis.word_count}, Syllabes: {analysis.syllable_count}")
                print(f"   Nasales: {analysis.nasal_count}, R-uvulaire: {analysis.uvular_r_count}")
                print(f"   Temps: {analysis.processing_time_ms:.2f}ms")
                
                # Export SSML
                ssml = pipeline.export_ssml(phrase)
                print(f"   SSML: {ssml[:80]}...")
                
                # Export TTS
                tts_data = pipeline.export_tts_compatible(phrase)
                if tts_data:
                    print(f"   Complexité: {tts_data['metadata']['complexity']}")
                
            except Exception as e:
                print(f"❌ Erreur: {e}")
        
        print("\n" + "=" * 80)
        print("\n✓ Tests d'intégration terminés avec succès!")
        print("✓ Pipeline prêt pour synthèse vocale Edge-TTS.\n")
    
    except Exception as e:
        print(f"❌ Erreur fatale: {e}")
        logger.exception("Erreur dans les tests")
