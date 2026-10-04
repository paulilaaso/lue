"""
Pipeline intégré Prosodie française + IPA.

Architecture :
    Texte
      ↓
    Prosodie française
      ↓
    IPA mot par mot
      ↓
    Liaisons / enchaînements inter-mots
      ↓
    Analyse enrichie
      ↓
    Export TTS / SSML

IMPORTANT :
- Le texte original n'est jamais modifié.
- Les liaisons sont appliquées uniquement à la représentation phonétique.
- Le FrenchPhoneticLinker reste dans phonetic_ipa.py.
- Aucun nouveau module n'est nécessaire.
- Le pipeline est résilient : une erreur IPA ne doit pas empêcher LUE de lire.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from .french_prosody import (
    BreathGroup,
    SpeechRegister,
    PhoneticAnalysis as ProsodyAnalysis,
    get_french_prosody_engine,
)

from .phonetic_ipa import (
    FrenchIPAPhoneticizer,
    IPAAnalysis,
    PhoneticSegment,
    get_ipa_phonetizer,
)


logger = logging.getLogger(__name__)


# ============================================================================
# STRUCTURES DE DONNÉES
# ============================================================================

@dataclass
class EnrichedPhoneticSegment:
    """Segment phonétique enrichi avec informations prosodiques."""

    grapheme: str
    ipa: str
    syllable_number: int

    is_stressed: bool = False
    emphasis_level: int = 0

    duration_ms: float = 0.0
    f0_base: float = 0.0


@dataclass
class FullPhoneticAnalysis:
    """Résultat complet du pipeline IPA + prosodie."""

    original_text: str

    # IPA finale, après liaisons/enchaînements.
    ipa_full: str = ""

    # Segments issus du phonétiseur.
    ipa_segments: List[PhoneticSegment] = field(default_factory=list)

    # Prosodie.
    processed_text: str = ""
    breath_groups: List[BreathGroup] = field(default_factory=list)

    enriched_segments: List[EnrichedPhoneticSegment] = field(
        default_factory=list
    )

    # Métadonnées.
    syllable_count: int = 0
    word_count: int = 0
    nasal_count: int = 0
    uvular_r_count: int = 0

    # Diagnostics.
    processing_time_ms: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ============================================================================
# PIPELINE
# ============================================================================

class ProsodyWithIPA:
    """
    Pipeline robuste :

        texte
          → prosodie
          → segmentation
          → IPA individuelle
          → liaison/enchaînement
          → export TTS

    Le pipeline ne modifie jamais le texte affiché à l'utilisateur.
    """

    MAX_TEXT_LENGTH = 100_000

    # Apostrophes typographiques reconnues.
    APOSTROPHES = "'’ʼ`"

    # Regex volontairement conservatrice :
    # - conserve les apostrophes dans d'un, l'ancien, qu'il...
    # - conserve les traits d'union dans dit-elle, États-Unis...
    WORD_RE = re.compile(
        r"[A-Za-zÀ-ÖØ-öø-ÿŒœÆæ]+(?:['’ʼ`][A-Za-zÀ-ÖØ-öø-ÿŒœÆæ]+)*"
        r"(?:-[A-Za-zÀ-ÖØ-öø-ÿŒœÆæ]+(?:['’ʼ`][A-Za-zÀ-ÖØ-öø-ÿŒœÆæ]+)*)*",
        re.UNICODE,
    )

    def __init__(
        self,
        formal_mode: bool = True,
        register: SpeechRegister = SpeechRegister.STANDARD,
        strict_mode: bool = False,
    ):
        self.formal_mode = formal_mode
        self.register = register
        self.strict_mode = strict_mode

        # Le moteur de prosodie est indépendant du phonétiseur.
        self.prosody_engine = get_french_prosody_engine(
            formal_mode=formal_mode,
            register=register,
            strict_mode=strict_mode,
        )

        # Le phonétiseur contient déjà le linker inter-mots.
        self.ipa_phonetizer = get_ipa_phonetizer()

        logger.info(
            "Pipeline Prosody+IPA initialisé "
            "(formal=%s, register=%s, strict=%s)",
            formal_mode,
            getattr(register, "value", register),
            strict_mode,
        )

    # ========================================================================
    # ANALYSE COMPLÈTE
    # ========================================================================

    def analyze_full(self, text: str) -> FullPhoneticAnalysis:
        """
        Analyse complète du texte.

        Le traitement est volontairement découpé en étapes indépendantes
        afin qu'une erreur de phonétisation ne fasse pas tomber toute la
        chaîne TTS.
        """

        start_time = time.perf_counter()

        if not isinstance(text, str):
            message = "Le texte fourni n'est pas une chaîne."

            if self.strict_mode:
                raise TypeError(message)

            return FullPhoneticAnalysis(
                original_text="" if text is None else str(text),
                warnings=[message],
            )

        if not text.strip():
            return FullPhoneticAnalysis(
                original_text=text,
                processed_text="",
                processing_time_ms=(
                    time.perf_counter() - start_time
                ) * 1000,
            )

        if len(text) > self.MAX_TEXT_LENGTH:
            message = (
                f"Texte trop long ({len(text)} caractères, "
                f"maximum {self.MAX_TEXT_LENGTH})."
            )

            if self.strict_mode:
                raise ValueError(message)

            logger.warning(message)

            text = text[: self.MAX_TEXT_LENGTH]

        warnings: List[str] = []

        # --------------------------------------------------------------------
        # 1. PROSODIE
        # --------------------------------------------------------------------

        prosody_result = self._analyze_prosody(text, warnings)

        processed_text = prosody_result.processed_text
        breath_groups = prosody_result.breath_groups

        # --------------------------------------------------------------------
        # 2. SEGMENTATION
        # --------------------------------------------------------------------

        words = self._extract_words(text)

        if not words:
            warnings.append("Aucun mot exploitable trouvé dans le texte.")

            return FullPhoneticAnalysis(
                original_text=text,
                processed_text=processed_text,
                breath_groups=breath_groups,
                warnings=warnings,
                processing_time_ms=(
                    time.perf_counter() - start_time
                ) * 1000,
            )

        # --------------------------------------------------------------------
        # 3. IPA MOT PAR MOT
        # --------------------------------------------------------------------

        word_ipas: List[str] = []
        analyses: List[IPAAnalysis] = []
        all_segments: List[PhoneticSegment] = []

        for word in words:
            try:
                ipa = self.ipa_phonetizer._convert_word_to_ipa(word)

                word_analysis = self.ipa_phonetizer.analyze_complete(word)

                # Le phonétiseur peut retourner une IPA vide sur un cas
                # inconnu. On conserve alors le mot sans faire tomber
                # toute la phrase.
                if not ipa:
                    warnings.append(
                        f"IPA vide pour le mot : {word!r}"
                    )
                    ipa = ""

                word_ipas.append(ipa)
                analyses.append(word_analysis)

                if word_analysis.segments:
                    all_segments.extend(word_analysis.segments)

            except Exception as exc:
                message = (
                    f"Échec IPA pour {word!r}: {exc}"
                )

                logger.warning(message, exc_info=True)
                warnings.append(message)

                # Important :
                # conserver l'alignement words ↔ word_ipas.
                word_ipas.append("")
                analyses.append(
                    IPAAnalysis(
                        original_text=word,
                        ipa_full="",
                    )
                )

        # --------------------------------------------------------------------
        # 4. LIAISONS / ENCHAÎNEMENTS
        # --------------------------------------------------------------------

        linked_ipas = self._apply_interword_linking(
            words=words,
            word_ipas=word_ipas,
            warnings=warnings,
        )

        # --------------------------------------------------------------------
        # 5. IPA FINALE
        # --------------------------------------------------------------------

        ipa_full = self._build_ipa_full(
            words=words,
            linked_ipas=linked_ipas,
        )

        # --------------------------------------------------------------------
        # 6. MÉTADONNÉES
        # --------------------------------------------------------------------

        syllable_total = sum(
            analysis.syllable_count
            for analysis in analyses
            if analysis.syllable_count
        )

        nasal_count = sum(
            1
            for segment in all_segments
            if "̃" in segment.ipa
        )

        uvular_r_count = sum(
            1
            for segment in all_segments
            if "ʁ" in segment.ipa
        )

        # --------------------------------------------------------------------
        # 7. ENRICHISSEMENT PROSODIQUE
        # --------------------------------------------------------------------

        enriched_segments = self._enrich_segments(
            all_segments,
            breath_groups,
            syllable_total,
        )

        processing_time_ms = (
            time.perf_counter() - start_time
        ) * 1000

        result = FullPhoneticAnalysis(
            original_text=text,
            ipa_full=ipa_full,
            ipa_segments=all_segments,
            processed_text=processed_text,
            breath_groups=breath_groups,
            enriched_segments=enriched_segments,
            syllable_count=syllable_total,
            word_count=len(words),
            nasal_count=nasal_count,
            uvular_r_count=uvular_r_count,
            processing_time_ms=processing_time_ms,
            warnings=warnings,
        )

        logger.debug(
            "IPA final : %s",
            ipa_full,
        )

        logger.info(
            "Analyse IPA complète : %d mots, %d syllabes, "
            "%.2f ms, %d avertissements",
            result.word_count,
            result.syllable_count,
            result.processing_time_ms,
            len(result.warnings),
        )

        return result

    # ========================================================================
    # PROSODIE
    # ========================================================================

    def _analyze_prosody(
        self,
        text: str,
        warnings: List[str],
    ) -> ProsodyAnalysis:
        """Analyse prosodique avec fallback résilient."""

        try:
            result = self.prosody_engine.analyze_phonetics_complete(text)

            if result is None:
                warnings.append(
                    "Le moteur de prosodie n'a retourné aucun résultat."
                )

                return ProsodyAnalysis(
                    original_text=text,
                    processed_text=text,
                    warnings=[
                        "Résultat prosodique vide."
                    ],
                )

            if result.warnings:
                warnings.extend(result.warnings)

            return result

        except Exception as exc:
            message = (
                f"Échec du moteur prosodique : {exc}"
            )

            logger.warning(message, exc_info=True)
            warnings.append(message)

            if self.strict_mode:
                raise

            # Fallback minimal : le texte original reste exploitable
            # par le TTS.
            return ProsodyAnalysis(
                original_text=text,
                processed_text=text,
                breath_groups=[],
                warnings=[message],
            )

    # ========================================================================
    # SEGMENTATION
    # ========================================================================

    def _extract_words(self, text: str) -> List[str]:
        """
        Extrait les unités lexicales sans perdre apostrophes et traits
        d'union nécessaires au traitement phonétique.

        Exemples :

            d'un ancien
            → ["d'un", "ancien"]

            dit-elle
            → ["dit-elle"]

            États-Unis
            → ["États-Unis"]
        """

        normalized = text.replace("’", "'")

        return [
            match.group(0).lower()
            for match in self.WORD_RE.finditer(normalized)
        ]

    # ========================================================================
    # LIAISONS
    # ========================================================================

    def _apply_interword_linking(
        self,
        words: List[str],
        word_ipas: List[str],
        warnings: List[str],
    ) -> List[str]:
        """
        Applique le linker déjà présent dans phonetic_ipa.py.

        C'est ici que se produisent notamment :

            comment + allez
                → kɔmɑ̃‿t

            les + États
                → le‿z

            d'un + ancien
                → dœ̃‿n

            dit-elle
                → di‿t ɛl
        """

        if not words:
            return []

        if len(words) != len(word_ipas):
            message = (
                "Désalignement words/IPA : "
                f"{len(words)} mots contre {len(word_ipas)} IPA."
            )

            logger.error(message)
            warnings.append(message)

            # Ne jamais tenter une transformation sur des listes
            # désalignées.
            return list(word_ipas)

        try:
            linked = self.ipa_phonetizer.link_words(
                words,
                word_ipas,
            )

            if not isinstance(linked, list):
                raise TypeError(
                    "link_words() n'a pas retourné une liste."
                )

            if len(linked) != len(words):
                raise ValueError(
                    "link_words() a modifié le nombre d'unités."
                )

            # Diagnostic explicite uniquement lorsqu'une liaison a
            # effectivement modifié l'IPA.
            for word, before, after in zip(
                words,
                word_ipas,
                linked,
            ):
                if before != after:
                    logger.debug(
                        "Liaison/enchaînement : %s : %s → %s",
                        word,
                        before,
                        after,
                    )

            return linked

        except Exception as exc:
            message = (
                f"Échec du linker phonétique : {exc}"
            )

            logger.warning(message, exc_info=True)
            warnings.append(message)

            if self.strict_mode:
                raise

            # Résilience : on conserve l'IPA individuelle.
            return list(word_ipas)

    # ========================================================================
    # CONSTRUCTION IPA
    # ========================================================================

    def _build_ipa_full(
        self,
        words: List[str],
        linked_ipas: List[str],
    ) -> str:
        """
        Construit la transcription IPA finale.

        Les unités vides sont ignorées afin d'éviter :
            // ou espaces multiples.
        """

        parts = []

        for ipa in linked_ipas:
            if not ipa:
                continue

            cleaned = ipa.strip()

            if cleaned:
                parts.append(cleaned)

        return " ".join(parts)

    # ========================================================================
    # SSML
    # ========================================================================

    def export_ssml(self, text: str) -> str:
        """
        Exporte une représentation SSML.

        IMPORTANT :
        on utilise l'analyse déjà calculée. On ne reconvertit PAS chaque
        groupe avec _convert_word_to_ipa(), sinon les liaisons calculées
        précédemment seraient perdues.

        Le texte original est conservé dans les phonèmes afin de permettre
        au moteur TTS de conserver une relation lisible entre texte et IPA.
        """

        try:
            analysis = self.analyze_full(text)

            if not analysis.original_text.strip():
                return "<speak></speak>"

            ssml_parts = [
                '<speak version="1.0" '
                'xmlns="http://www.w3.org/2001/10/synthesis" '
                'xml:lang="fr-FR">'
            ]

            # Si aucun groupe de souffle n'a été détecté, fallback propre.
            if not analysis.breath_groups:
                escaped_text = html.escape(
                    analysis.original_text
                )

                ssml_parts.append(
                    f"<prosody>{escaped_text}</prosody>"
                )
                ssml_parts.append("</speak>")

                return "".join(ssml_parts)

            # ----------------------------------------------------------------
            # IMPORTANT :
            # Le moteur actuel ne fournit pas encore un mapping fiable
            # groupe de souffle → tranche exacte de l'IPA finale.
            #
            # On utilise donc ici le texte prosodique pour les groupes et
            # l'IPA globale comme information diagnostique, plutôt que de
            # fabriquer une fausse correspondance phonème/groupe.
            # ----------------------------------------------------------------

            # Pour le moment, une seule unité phonétique globale est plus
            # fiable qu'une série de phonèmes mal alignés.
            ipa_value = analysis.ipa_full.strip()

            for group in analysis.breath_groups:
                rate = "1.0"
                pitch = "0%"

                if group.emphasis_level == 2:
                    rate = "0.90"
                    pitch = "+10%"
                elif group.emphasis_level == 1:
                    pitch = "+5%"

                group_text = html.escape(
                    group.text.strip()
                )

                if not group_text:
                    continue

                ssml_parts.append(
                    f'<prosody rate="{rate}" pitch="{pitch}">'
                )

                # Nous n'injectons l'IPA que si elle est disponible.
                #
                # Pour éviter de casser le moteur avec une IPA vide,
                # on garde le texte comme fallback.
                if ipa_value:
                    # IPA XML-safe.
                    safe_ipa = html.escape(
                        ipa_value,
                        quote=True,
                    )

                    safe_text = html.escape(
                        group.text.strip()
                    )

                    ssml_parts.append(
                        '<phoneme alphabet="ipa" '
                        f'ph="{safe_ipa}">'
                        f"{safe_text}"
                        "</phoneme>"
                    )
                else:
                    ssml_parts.append(group_text)

                if group.text.rstrip().endswith(("!", "?")):
                    ssml_parts.append(
                        '<break time="500ms"/>'
                    )
                elif group.text.rstrip().endswith(","):
                    ssml_parts.append(
                        '<break time="200ms"/>'
                    )

                ssml_parts.append("</prosody>")

            ssml_parts.append("</speak>")

            return "".join(ssml_parts)

        except Exception as exc:
            logger.error(
                "Erreur export SSML : %s",
                exc,
                exc_info=True,
            )

            if self.strict_mode:
                raise

            # Fallback le plus sûr : texte brut.
            return html.escape(text or "")

    # ========================================================================
    # EXPORT TTS
    # ========================================================================

    def export_tts_compatible(
        self,
        text: str,
    ) -> Dict[str, Union[str, List, Dict]]:
        """
        Retourne les données consommables par le reste de LUE.

        L'interface reste compatible avec reader.py :

            data["ssml"]
            data["text"]
            data["processed_text"]
            data["ipa"]
        """

        try:
            analysis = self.analyze_full(text)

            complexity = self._assess_complexity(
                analysis
            )

            ssml = self.export_ssml(
                analysis.original_text
            )

            return {
                "text": analysis.original_text,

                "text_processed": (
                    analysis.processed_text
                    or analysis.original_text
                ),

                "processed_text": (
                    analysis.processed_text
                    or analysis.original_text
                ),

                "ipa": analysis.ipa_full,

                "ssml": ssml,

                "segments": [
                    {
                        "grapheme": segment.grapheme,
                        "ipa": segment.ipa,
                        "syllable": segment.syllable_number,
                        "stressed": segment.is_stressed,
                    }
                    for segment in analysis.enriched_segments[:50]
                ],

                "breath_groups": [
                    {
                        "text": group.text,
                        "intonation": getattr(
                            group.intonation,
                            "value",
                            str(group.intonation),
                        ),
                        "emphasis": group.emphasis_level,
                        "syllables": group.syllable_count,
                    }
                    for group in analysis.breath_groups
                ],

                "metadata": {
                    "language": "fr-FR",
                    "syllables": analysis.syllable_count,
                    "words": analysis.word_count,
                    "nasals": analysis.nasal_count,
                    "uvular_r": analysis.uvular_r_count,
                    "complexity": complexity,
                    "processing_time_ms": (
                        analysis.processing_time_ms
                    ),
                    "warnings": list(analysis.warnings),
                    "ipa_linking_enabled": True,
                },
            }

        except Exception as exc:
            logger.error(
                "Erreur export TTS : %s",
                exc,
                exc_info=True,
            )

            if self.strict_mode:
                raise

            # Interface de fallback compatible avec reader.py.
            return {
                "text": text or "",
                "text_processed": text or "",
                "processed_text": text or "",
                "ipa": "",
                "ssml": html.escape(text or ""),
                "segments": [],
                "breath_groups": [],
                "metadata": {
                    "language": "fr-FR",
                    "syllables": 0,
                    "words": 0,
                    "nasals": 0,
                    "uvular_r": 0,
                    "complexity": "unknown",
                    "processing_time_ms": 0.0,
                    "warnings": [str(exc)],
                    "ipa_linking_enabled": False,
                },
            }

    # ========================================================================
    # SEGMENTS / PROSODIE
    # ========================================================================

    def _enrich_segments(
        self,
        segments: List[PhoneticSegment],
        breath_groups: List[BreathGroup],
        total_syllables: int,
    ) -> List[EnrichedPhoneticSegment]:
        """
        Enrichit les segments phonétiques avec les informations prosodiques.

        Le mapping reste volontairement prudent : sans mapping caractère →
        syllabe fourni par le moteur de prosodie, on ne prétend pas calculer
        un alignement parfait.
        """

        enriched: List[EnrichedPhoneticSegment] = []

        if not segments:
            return enriched

        # Construire les bornes cumulées des groupes.
        cumulative_groups = []
        cumulative = 0

        for group in breath_groups:
            start = cumulative + 1
            cumulative += max(
                0,
                int(group.syllable_count or 0),
            )

            cumulative_groups.append(
                (start, cumulative, group)
            )

        for segment in segments:
            group = self._find_group_for_syllable(
                segment.syllable_number,
                cumulative_groups,
            )

            enriched.append(
                EnrichedPhoneticSegment(
                    grapheme=segment.grapheme,
                    ipa=segment.ipa,
                    syllable_number=segment.syllable_number,
                    is_stressed=bool(
                        group and group.emphasis_level > 0
                    ),
                    emphasis_level=(
                        group.emphasis_level
                        if group
                        else 0
                    ),
                )
            )

        return enriched

    @staticmethod
    def _find_group_for_syllable(
        syllable_number: int,
        cumulative_groups,
    ) -> Optional[BreathGroup]:
        """Trouve prudemment le groupe correspondant à une syllabe."""

        for start, end, group in cumulative_groups:
            if start <= syllable_number <= end:
                return group

        return None

    # ========================================================================
    # COMPLEXITÉ
    # ========================================================================

    def _assess_complexity(
        self,
        analysis: FullPhoneticAnalysis,
    ) -> str:
        """Évalue grossièrement la complexité phonétique."""

        score = 0.0

        score += analysis.nasal_count * 1.0
        score += analysis.uvular_r_count * 0.5
        score += analysis.syllable_count * 0.1

        # Les avertissements augmentent légèrement le niveau de complexité,
        # sans rendre l'évaluation artificiellement énorme.
        score += min(
            len(analysis.warnings) * 0.5,
            3.0,
        )

        if score <= 1:
            return "very_easy"

        if score <= 2:
            return "easy"

        if score <= 4:
            return "medium"

        if score <= 6:
            return "difficult"

        return "very_difficult"


# ============================================================================
# SINGLETON
# ============================================================================

_pipeline: Optional[ProsodyWithIPA] = None


def get_prosody_ipa_pipeline(
    formal_mode: bool = True,
    register: SpeechRegister = SpeechRegister.STANDARD,
    strict_mode: bool = False,
) -> ProsodyWithIPA:
    """
    Retourne l'instance singleton du pipeline.

    Le singleton est pratique pour LUE car les dictionnaires du phonétiseur
    sont coûteux à initialiser.
    """

    global _pipeline

    if _pipeline is None:
        _pipeline = ProsodyWithIPA(
            formal_mode=formal_mode,
            register=register,
            strict_mode=strict_mode,
        )

    return _pipeline


def reset_prosody_ipa_pipeline() -> None:
    """Réinitialise explicitement le singleton."""

    global _pipeline
    _pipeline = None

    logger.info(
        "Pipeline Prosody+IPA réinitialisé."
    )


# ============================================================================
# TESTS LOCAUX
# ============================================================================

def _run_diagnostic_tests() -> None:
    """
    Tests simples du pipeline.

    Ces tests ne vérifient PAS la qualité acoustique du TTS.
    Ils vérifient que les liaisons sont effectivement présentes dans
    l'IPA produite par le pipeline.
    """

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s - %(name)s - %(message)s",
    )

    pipeline = ProsodyWithIPA(
        formal_mode=True,
        register=SpeechRegister.FORMAL,
        strict_mode=False,
    )

    tests = [
        "Bonjour, comment allez-vous ?",
        "Les États-Unis d'Amérique...",
        "« Bonjour ! » dit-elle.",
        "Aujourd'hui, j'écoute l'histoire d'un ancien élève.",
    ]

    print()
    print("=" * 80)
    print("DIAGNOSTIC PROSODIE + IPA")
    print("=" * 80)

    for text in tests:
        print()
        print("TEXTE :", text)

        result = pipeline.analyze_full(text)

        print("MOTS  :", pipeline._extract_words(text))
        print("IPA   :", result.ipa_full)

        if result.warnings:
            print("WARN  :", result.warnings)

        print(
            "STATS :",
            f"{result.word_count} mots,",
            f"{result.syllable_count} syllabes,",
            f"{result.processing_time_ms:.2f} ms",
        )

    print()
    print("=" * 80)
    print("FIN DU DIAGNOSTIC")
    print("=" * 80)


if __name__ == "__main__":
    _run_diagnostic_tests()
