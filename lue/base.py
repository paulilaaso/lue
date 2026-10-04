"""Abstract base class for TTS models in the Lue eBook reader."""

import asyncio
from abc import ABC, abstractmethod
import hashlib
import importlib
import json
import logging
import os
import re
import shutil
import uuid
from typing import Optional, List, Tuple, Any
from rich.console import Console

from .. import audio, config, timing_calculator

# ════════════════════════════════════════════════════════════════════════════
# PROSODIE / IPA : modules OPTIONNELS
#
#   french_prosody.py, english_prosody.py, phonetic_ipa.py, prosody_with_ipa.py
#
# Aucun n'est requis. Niveaux de préparation du texte TTS, par langue :
#   off     (basique)  : texte normalisé seulement
#   prosody (prosodie) : french_prosody.py / english_prosody.py présents
#   ipa     (luxe)     : prosody_with_ipa.py + phonetic_ipa.py + french_prosody.py (FR)
# Réglage : config.PROSODY_LEVEL = "auto" | "ipa" | "prosody" | "off"
# (« auto » = le meilleur niveau disponible ; les autres valeurs sont un plafond).
# Un niveau qui échoue PROSODY_MAX_FAILURES fois de suite est désactivé pour la
# session : on retombe sur le niveau inférieur.
# ════════════════════════════════════════════════════════════════════════════

_LEVEL_RANK = {"off": 0, "prosody": 1, "ipa": 2}
_LEVEL_LABEL = {"off": "basique", "prosody": "prosodie", "ipa": "luxe (prosodie + IPA)"}

_caps = None          # détection paresseuse, une seule fois
_failures = {}        # (lang, niveau) -> échecs consécutifs
_disabled = set()     # (lang, niveau) coupés pour la session


def _optional(module, *attrs):
    """Importe un module optionnel du paquet. Retourne le tuple des attributs
    demandés, ou None si absent / inutilisable (jamais d'exception)."""
    try:
        mod = importlib.import_module(f"..{module}", package=__package__)
        return tuple(getattr(mod, a) for a in attrs)
    except ModuleNotFoundError as exc:
        logging.info("Module optionnel absent (%s) : %s", module, exc)
    except Exception as exc:  # SyntaxError, NameError, AttributeError... : module cassé
        logging.warning("Module optionnel '%s' inutilisable : %s", module, exc)
    return None


def _capabilities() -> dict:
    global _caps
    if _caps is None:
        fr = _optional("french_prosody", "get_french_prosody_engine", "SpeechRegister")
        _caps = {
            "fr": fr,
            "ipa": _optional("prosody_with_ipa", "get_prosody_ipa_pipeline") if fr else None,
            "en": _optional("english_prosody", "get_english_prosody_engine"),
        }
    return _caps


def _level_available(lang: str, level: str) -> bool:
    caps = _capabilities()
    if lang == "fr":
        return bool(caps["ipa"] if level == "ipa" else caps["fr"] if level == "prosody" else False)
    return level == "prosody" and bool(caps["en"])


def _candidate_levels(lang: str) -> list:
    """Niveaux utilisables pour cette langue, du meilleur au plus simple."""
    requested = str(getattr(config, "PROSODY_LEVEL", "auto")).lower()
    ceiling = _LEVEL_RANK.get(requested, _LEVEL_RANK["ipa"])  # auto/inconnu : pas de plafond
    return [
        lvl for lvl in ("ipa", "prosody")
        if _LEVEL_RANK[lvl] <= ceiling
        and (lang, lvl) not in _disabled
        and _level_available(lang, lvl)
    ]


def get_prosody_level(language=None, voice=None) -> str:
    """Niveau actuellement actif ('off', 'prosody' ou 'ipa') pour cette langue."""
    levels = _candidate_levels(resolve_language(language, voice))
    return levels[0] if levels else "off"


def _run_level(level: str, lang: str, text: str) -> str:
    caps = _capabilities()
    if level == "ipa":
        get_pipeline = caps["ipa"][0]
        speech_register = caps["fr"][1]
        data = get_pipeline(
            formal_mode=True, register=speech_register.FORMAL
        ).export_tts_compatible(text)
        for key in ("text_processed", "processed_text", "text"):
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                return value
        raise ValueError("pipeline IPA : sortie vide")
    if lang == "fr":
        return caps["fr"][0]().prepare_for_tts(text) or text
    return caps["en"][0]().prepare_for_tts(text) or text


def init_prosody(language=None, voice=None) -> str:
    """Préchauffe les moteurs (dictionnaires coûteux) et retourne une ligne de
    statut lisible, ex. « Texte TTS [FR] : luxe (prosodie + IPA) »."""
    lang = resolve_language(language, voice)
    if _candidate_levels(lang):
        prepare_text_chunk("Bonjour." if lang == "fr" else "Hello.", language, voice)
    label = _LEVEL_LABEL[get_prosody_level(language, voice)]
    requested = str(getattr(config, "PROSODY_LEVEL", "auto")).lower()
    forced = "" if requested == "auto" else f" (PROSODY_LEVEL={requested})"
    return f"Texte TTS [{lang.upper()}] : {label}{forced}"


def resolve_language(lang, voice=None) -> str:
    """Déduit 'fr' ou 'en' du code langue (fr-FR, f, a, b...) ou de la voix."""
    code = (lang or "").lower()
    if code.startswith("fr") or code == "f":
        return "fr"
    if code.startswith("en") or code in ("a", "b"):
        return "en"
    v = (voice or "").lower()
    if v.startswith(("fr-", "ff_", "fm_")):
        return "fr"
    return "en"


def normalize_text(text: str, language: str = "en") -> str:
    """
    Nettoie les artefacts ePub courants (notes de bas de page, liens, 
    multi-espaces, ponctuation aberrante) pour fluidifier la lecture TTS.
    """
    if not text:
        return ""
    
    # 1. Supprime les notes de bas de page entre crochets [1], [2], etc.
    cleaned = re.sub(r'\[\d+\]', '', text)
    
    # 2. Normalise les abréviations selon la langue
    lang = language.lower() if language else "en"
    if lang.startswith("fr"):
        cleaned = re.sub(r'\bDr\.\s*', 'Docteur ', cleaned)
        cleaned = re.sub(r'\bM\.\s*', 'Monsieur ', cleaned)
        cleaned = re.sub(r'\bMme\.\s*', 'Madame ', cleaned)
        cleaned = re.sub(r'\bProf\.\s*', 'Professeur ', cleaned)
    else:
        cleaned = re.sub(r'\bDr\.\s*', 'Doctor ', cleaned)
        cleaned = re.sub(r'\bMr\.\s*', 'Mister ', cleaned)
        cleaned = re.sub(r'\bMrs\.\s*', 'Missus ', cleaned)
        cleaned = re.sub(r'\bProf\.\s*', 'Professor ', cleaned)
    
    # 3. Supprime les URL ou liens
    cleaned = re.sub(r'http[s]?://\S+', '', cleaned)
    
    # 4. Nettoie les espaces multiples
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned


_COMMA_RUN = re.compile(r",(?:\s*,)+")
_COMMA_BEFORE_END = re.compile(r",\s*([.!?…;:])")


def _tidy_punctuation(text: str) -> str:
    """Nettoie les artefacts de ponctuation laissés par les moteurs de prosodie
    (',,' -> ',' ; ', .' -> '.')."""
    text = _COMMA_RUN.sub(",", text)
    return _COMMA_BEFORE_END.sub(r"\1", text)


def prepare_text_chunk(text: str, language=None, voice=None) -> str:
    """Point d'entrée UNIQUE de préparation du texte pour le TTS."""
    return _tidy_punctuation(_prepare_text_chunk_raw(text, language, voice))


def _prepare_text_chunk_raw(text: str, language=None, voice=None) -> str:
    if not text:
        return ""
    lang = resolve_language(language, voice)
    normalized = normalize_text(text, lang)
    if not normalized:
        return ""
    max_failures = max(1, int(getattr(config, "PROSODY_MAX_FAILURES", 5)))
    for level in _candidate_levels(lang):
        key = (lang, level)
        try:
            result = _run_level(level, lang, normalized)
            _failures[key] = 0
            return result
        except Exception as exc:
            _failures[key] = _failures.get(key, 0) + 1
            if _failures[key] >= max_failures:
                _disabled.add(key)
                logging.warning(
                    "Niveau '%s' (%s) désactivé pour la session après %d échecs : %s",
                    level, lang, _failures[key], exc)
            else:
                logging.warning(
                    "Niveau '%s' (%s) en échec, repli sur le niveau inférieur : %s",
                    level, lang, exc)
    return normalized


def prune_audio_cache(cache_dir: Optional[str] = None, max_size_mb: int = 500) -> None:
    """Purge les plus anciens fichiers du cache audio au-delà de max_size_mb 
    et nettoie les fichiers temporaires orphelins (.tmp)."""
    try:
        if not cache_dir:
            cache_dir = getattr(config, "AUDIO_CACHE_DIR", os.path.join(getattr(config, "AUDIO_DATA_DIR", "."), "cache"))
        if not os.path.exists(cache_dir):
            return
        
        import time
        current_time = time.time()
        max_bytes = max_size_mb * 1024 * 1024
        files = []
        total_size = 0
        
        for entry in os.scandir(cache_dir):
            if entry.is_file():
                # Nettoyage automatique des fichiers .tmp orphelins de plus d'une heure (3600s)
                if entry.name.endswith(".tmp"):
                    try:
                        if current_time - entry.stat().st_mtime > 3600:
                            os.remove(entry.path)
                    except OSError:
                        pass
                    continue

                stat = entry.stat()
                files.append((entry.path, stat.st_size, stat.st_mtime))
                total_size += stat.st_size
        
        if total_size <= max_bytes:
            return
        
        # Trier par date de modification (les plus anciens d'abord)
        files.sort(key=lambda x: x[2])
        
        for path, size, _ in files:
            if total_size <= max_bytes:
                break
            try:
                os.remove(path)
                timings_path = f"{path}.timings.json"
                if os.path.exists(timings_path):
                    os.remove(timings_path)
                total_size -= size
            except OSError:
                pass
    except Exception as e:
        logging.warning(f"Erreur lors de la purge du cache audio : {e}")


class TTSBase(ABC):
    """
    Abstract base class for all TTS models.
    
    This class defines the interface that all TTS models must implement
    to be compatible with the Lue eBook reader.
    """

    timings_include_audio = False  # True si get_raw_timing_data() écrit aussi l'audio

    def __init__(self, console: Console, voice: Optional[str] = None, lang: Optional[str] = None):
        """
        Initialize the TTS model.
        
        Args:
            console: Rich console instance for user feedback
            voice: Optional voice for the TTS model
            lang: Optional language for the TTS model
        """
        self.console = console
        self.voice = voice
        self.lang = lang
        self.initialized = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the unique identifier for this TTS model."""
        pass

    @property
    @abstractmethod
    def output_format(self) -> str:
        """Get the audio format this model produces (e.g. 'mp3', 'wav')."""
        pass

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the TTS model asynchronously."""
        pass

    @abstractmethod
    async def generate_audio(self, text: str, output_path: str):
        """Generate audio from text and save to file."""
        pass

    def _get_cache_file_path(self, text: str) -> Optional[str]:
        """
        Génère un chemin de cache unique basé sur le hash SHA-256 du texte, de la voix et de la langue.
        """
        try:
            cache_dir = getattr(config, "AUDIO_CACHE_DIR", os.path.join(getattr(config, "AUDIO_DATA_DIR", "."), "cache"))
            os.makedirs(cache_dir, exist_ok=True)
            
            unique_string = f"v{getattr(config, 'TTS_CACHE_VERSION', 1)}_{self.name}_{self.voice}_{self.lang}_{text}"
            text_hash = hashlib.sha256(unique_string.encode('utf-8')).hexdigest()
            
            return os.path.join(cache_dir, f"{text_hash}.{self.output_format}")
        except Exception as e:
            logging.warning(f"Impossible de déterminer le chemin du cache : {e}")
            return None

    @staticmethod
    def _read_cache(cache_path, timings_path, output_path):
        try:
            if os.path.getsize(cache_path) <= 0 or not os.path.exists(timings_path):
                return None
            with open(timings_path, "r", encoding="utf-8") as f:
                raw = [(w, float(st), float(en)) for w, st, en in json.load(f)]
            shutil.copy2(cache_path, output_path)
            os.utime(cache_path, None)
            return raw
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _write_cache(output_path, cache_path, timings_path, raw_timings):
        try:
            if os.path.getsize(output_path) <= 0:
                return
            uid = f"{os.getpid()}.{uuid.uuid4().hex}"
            tmp_audio = f"{cache_path}.{uid}.tmp"
            tmp_json = f"{timings_path}.{uid}.tmp"
            try:
                with open(tmp_json, "w", encoding="utf-8") as f:
                    json.dump(raw_timings, f)
                shutil.copy2(output_path, tmp_audio)
                os.replace(tmp_json, timings_path)
                os.replace(tmp_audio, cache_path)
            finally:
                # Nettoyage explicite des fichiers temporaires s'ils existent encore (en cas d'échec avant os.replace)
                for tmp in (tmp_json, tmp_audio):
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            prune_audio_cache()
        except OSError as e:
            logging.warning(f"Erreur lors de la sauvegarde dans le cache audio : {e}")

    def _synthesis_settings(self):
        cfg = getattr(config, "TTS_SYNTHESIS", {}).get(self.name, {})
        return cfg.get("timeout"), max(0, int(cfg.get("retries", 0)))

    async def _synthesize_once(self, text, output_path):
        if not self.timings_include_audio:
            await self.generate_audio(text, output_path)
        return await self.get_raw_timing_data(text, output_path)

    async def _synthesize(self, text, output_path):
        """Synthèse avec timeout et nouvelles tentatives (réglages : config.TTS_SYNTHESIS)."""
        timeout, retries = self._synthesis_settings()
        last_exc = None
        for attempt in range(retries + 1):
            try:
                coro = self._synthesize_once(text, output_path)
                return await (asyncio.wait_for(coro, timeout) if timeout else coro)
            except Exception as e:  # CancelledError n'est pas capturée (BaseException)
                last_exc = e
                logging.warning(
                    "Synthèse %s : tentative %d/%d échouée (%s)",
                    self.name, attempt + 1, retries + 1, type(e).__name__)
                if attempt < retries:
                    await asyncio.sleep(min(2 ** attempt, 4))
        try:
            os.remove(output_path)  # évite de laisser un fichier audio partiel
        except OSError:
            pass
        raise last_exc

    async def generate_audio_guarded(self, text, output_path):
        """generate_audio() avec le même timeout que la synthèse principale."""
        timeout, _ = self._synthesis_settings()
        coro = self.generate_audio(text, output_path)
        await (asyncio.wait_for(coro, timeout) if timeout else coro)

    async def generate_audio_with_timing(self, text: str, output_path: str) -> Any:
        processed_text = prepare_text_chunk(text, self.lang, self.voice)
        cache_path = self._get_cache_file_path(processed_text)
        timings_path = f"{cache_path}.timings.json" if cache_path else None
        raw_timings = None
        if cache_path:
            raw_timings = await asyncio.to_thread(
                self._read_cache, cache_path, timings_path, output_path)
        if raw_timings is None:
            raw_timings = await self._synthesize(processed_text, output_path)
            if cache_path:
                await asyncio.to_thread(
                    self._write_cache, output_path, cache_path, timings_path, raw_timings)
        duration = await audio.get_audio_duration(output_path)
        return timing_calculator.process_tts_timing_data(text, raw_timings, duration)

    async def get_raw_timing_data(self, text: str, output_path: str) -> List[Tuple[str, float, float]]:
        """Get raw timing data from the TTS engine."""
        return []

    async def warm_up(self):
        """Warm up the model to reduce initial latency."""
        pass

    def get_overlap_seconds(self) -> Optional[float]:
        """Get the TTS-specific overlap seconds for this model."""
        return config.TTS_OVERLAP_SECONDS.get(self.name)