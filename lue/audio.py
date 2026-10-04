"""
Audio management, text cleaning, and playback/producer loops for the Lue eBook reader.

Version corrigée :
- Chaque position (chapitre, paragraphe, phrase) possède son propre fichier audio
  temporaire unique : plus de recyclage de buffers partagés entre producteur,
  prefetch et lecteur (supprime les courses d'écriture/lecture/suppression).
- Le cache AudioPipeline est propriétaire du cycle de vie des fichiers
  (suppression à l'éviction et au nettoyage uniquement).
- Le lecteur poste l'événement '_new_sentence_started' même lorsqu'un élément
  doit être sauté, pour que la surbrillance ne se fige jamais.
- stop_and_clear_audio ne touche plus à reader.running (drapeau de vie de
  l'application) : l'ancienne bascule False puis True après >=50 ms d'attentes
  tuait les boucles UI (_ui_update_loop / _word_update_loop tournent sur
  'while self.running'), ce qui figeait la surbrillance sur la première
  phrase. L'arrêt audio se fait uniquement via task.cancel() et le kill
  des processus.
"""

import asyncio
import subprocess
import os
import re
import logging
import tempfile
from collections import OrderedDict
from functools import lru_cache
from typing import Optional, Any, Dict, Tuple

from . import config, content_parser

# Pattern pour le nettoyage et la détection de fragments d'abréviations
ABBREVIATION_PATTERN = r'\b(Mr|Mrs|Ms|Dr|Prof|Rev|Hon|Jr|Sr|Cpl|Sgt|Gen|Col|Capt|Lt|Pvt|vs|viz|Co|Inc|Ltd|Corp|St|Ave|Blvd)\.'
INITIAL_PATTERN = r'\b([A-Z])\.(?=\s[A-Z])'

COMPILED_ABBR_REGEX = re.compile(ABBREVIATION_PATTERN)
COMPILED_INIT_REGEX = re.compile(INITIAL_PATTERN)
# Ponctuation « flottante » (artefacts d'extraction : " . ", " , ") à supprimer.
# Les points de suspension (2 points ou plus) sont conservés : ils portent une pause.
COMPILED_PUNCT_REGEX = re.compile(r'(?:^|\s)(?!\.{2,})[.,]+(?=\s|$)')
# Ponctuation haute (? ! : ;) précédée d'une espace, souvent insécable en
# typographie française : on la RATTACHE au mot précédent. L'ancien regex la
# supprimait ("Comment allez-vous ?" → "Comment allez-vous"), donc plus
# d'intonation interrogative ni de pause avant la prosodie.
COMPILED_SPACED_HIGH_PUNCT_REGEX = re.compile(r'\s+([?!:;]+)(?=[\s»”)\]"\']|$)')
COMPILED_LEADING_HIGH_PUNCT_REGEX = re.compile(r'^\s*[?!:;]+(?=\s|$)\s*')
COMPILED_DASH_REGEX = re.compile(r'(?:^|\s)-(?=")')
COMPILED_WHITESPACE_REGEX = re.compile(r'\s+')

from .timing_calculator import process_tts_timing_data


def _is_french_tts(tts: Any) -> bool:
    """Le moteur TTS courant parle-t-il français ? (langue, voix, puis config)"""
    lang = (getattr(tts, "lang", None) or "").lower()
    voice = (getattr(tts, "voice", None) or "").lower()
    if not lang and not voice:
        lang = str(config.TTS_LANGUAGE_CODES.get(getattr(tts, "name", ""), "")).lower()
    return (
        lang == "f"
        or lang.startswith("fr")
        or voice.startswith(("fr-", "fr_", "ff_", "fm_"))
    )


@lru_cache(maxsize=1024)
def clean_tts_text(text: str, french: bool = False) -> str:
    """
    Nettoie le texte avant synthèse.

    - Hors français : retire le point des abréviations anglaises et des
      initiales (évite des pauses artificielles).
    - Français (french=True) : ces points sont CONSERVÉS, car la prosodie
      française les attend pour développer « M. », « Mme. », « Dr. »…
      (sinon « M. Dupont » devient « M Dupont », lu « emme »).
    - Dans tous les cas : supprime la ponctuation flottante (" . ", " , ") et
      rattache ? ! : ; à leur mot au lieu de les effacer.
    """
    if not text:
        return ""

    if not french:
        text = COMPILED_ABBR_REGEX.sub(r'\1', text)
        text = COMPILED_INIT_REGEX.sub(r'\1 ', text)
    text = COMPILED_PUNCT_REGEX.sub(' ', text)
    text = COMPILED_LEADING_HIGH_PUNCT_REGEX.sub('', text)
    text = COMPILED_SPACED_HIGH_PUNCT_REGEX.sub(r'\1', text)
    text = COMPILED_DASH_REGEX.sub(' ', text)
    text = COMPILED_WHITESPACE_REGEX.sub(' ', text).strip()

    return text


FFPROBE_TIMEOUT = 5.0
PROCESS_WAIT_TIMEOUT = 0.2
PROCESS_KILL_TIMEOUT = 0.1


async def _wait_for_process(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
) -> bool:
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _terminate_process(
    process: asyncio.subprocess.Process,
    *,
    terminate_timeout: float = PROCESS_WAIT_TIMEOUT,
    kill_timeout: float = PROCESS_KILL_TIMEOUT,
) -> None:
    if process.returncode is not None:
        return

    try:
        process.terminate()
    except ProcessLookupError:
        return

    if await _wait_for_process(process, timeout=terminate_timeout):
        return

    try:
        process.kill()
    except ProcessLookupError:
        return

    await _wait_for_process(process, timeout=kill_timeout)


async def _remove_file_with_retries(
    file_path: str,
    *,
    attempts: int = 5,
    delay: float = 0.08,
) -> None:
    """Remove a generated audio file with robust backoff to tolerate OS/ffplay file locks."""
    for attempt in range(attempts):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        except (PermissionError, OSError):
            if attempt + 1 < attempts:
                await asyncio.sleep(delay * (attempt + 1))
            else:
                logging.debug("Could not remove temp audio file after %d attempts: %s", attempts, file_path)


def _audio_file_path(reader: Any, pos: Tuple[int, int, int]) -> str:
    """Chemin de fichier unique et stable pour une position donnée.

    Une position (c, p, s) correspond toujours au même fichier : le producteur et
    le prefetch n'écrivent donc jamais dans le fichier d'une autre phrase, et la
    suppression d'un fichier n'invalide jamais une autre entrée du cache.
    """
    c, p, s = pos
    output_format = getattr(reader.tts_model, 'output_format', 'mp3') if reader.tts_model else "mp3"
    return os.path.join(
        tempfile.gettempdir(),
        f"lue_audio_{os.getpid()}_{c}_{p}_{s}.{output_format}",
    )


class AudioPipeline:
    """
    Pipeline audio unifié : cache par position (chapitre, paragraphe, phrase),
    dédoublonnage des générations en cours et pré-chargement en arrière-plan.

    Le pipeline est propriétaire des fichiers qu'il met en cache : ils ne sont
    supprimés qu'à l'éviction du cache ou lors du nettoyage explicite, jamais
    pendant la lecture (le lecteur ne supprime plus les fichiers).
    """

    MAX_CACHE_ENTRIES = 64

    def __init__(self, reader):
        self.reader = reader
        # pos -> (path, duration, timing_info) ; insertion ordonnée pour l'éviction LRU
        self._audio_cache: "OrderedDict[Tuple[int, int, int], Tuple[str, float, dict]]" = OrderedDict()
        self._generation_tasks: Dict[Tuple[int, int, int], asyncio.Task] = {}
        self._cache_lock = asyncio.Lock()

    def _evict_locked(self) -> None:
        """Évince les entrées les plus anciennes si le cache dépasse sa taille maximale."""
        while len(self._audio_cache) > self.MAX_CACHE_ENTRIES:
            _, (old_path, _, _) = self._audio_cache.popitem(last=False)
            if old_path:
                asyncio.create_task(_remove_file_with_retries(old_path))

    def _build_tts_text(self, pos: Tuple[int, int, int]) -> Optional[str]:
        """Construit le texte à synthétiser pour une position (gère les abréviations)."""
        c, p, s = pos
        try:
            sentences = content_parser.split_into_sentences(self.reader.chapters[c][p])
            text = sentences[s]
        except IndexError:
            return None

        if not text or not text.strip():
            return None

        is_abbrev_fragment = bool(COMPILED_ABBR_REGEX.fullmatch(text.strip()))
        if is_abbrev_fragment and s + 1 < len(sentences):
            text += " " + sentences[s + 1]

        return text

    async def _generate(self, pos: Tuple[int, int, int]) -> Optional[Tuple[str, float, dict]]:
        """Génère l'audio pour une position et l'ajoute au cache. Retourne l'entrée ou None."""
        try:
            text = self._build_tts_text(pos)
            if not text:
                return None

            output_filename = _audio_file_path(self.reader, pos)
            await _remove_file_with_retries(output_filename)

            sanitized_text = clean_tts_text(text, _is_french_tts(self.reader.tts_model))
            if not sanitized_text:
                return None

            timing_info = None
            tts = self.reader.tts_model
            if hasattr(tts, 'generate_audio_with_timing'):
                try:
                    timing_info = await tts.generate_audio_with_timing(sanitized_text, output_filename)
                except Exception:
                    logging.warning("generate_audio_with_timing a échoué pour %s ; repli sans timings", pos, exc_info=True)
                    guarded = getattr(tts, "generate_audio_guarded", tts.generate_audio)
                    await guarded(sanitized_text, output_filename)
            else:
                await tts.generate_audio(sanitized_text, output_filename)

            duration = await get_audio_duration(output_filename)
            if duration is None or duration <= 0:
                await _remove_file_with_retries(output_filename)
                return None

            if timing_info is None:
                timing_info = process_tts_timing_data(text, [], duration)

            async with self._cache_lock:
                self._audio_cache[pos] = (output_filename, duration, timing_info)
                self._evict_locked()

            return (output_filename, duration, timing_info)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"Erreur génération audio pour position {pos}: {e}", exc_info=True)
            return None

    async def get_or_generate(self, pos: Tuple[int, int, int]) -> Optional[Tuple[str, float, dict]]:
        """Retourne l'entrée audio du cache, ou la génère (une seule génération à la fois)."""
        pending = None
        async with self._cache_lock:
            entry = self._audio_cache.get(pos)
            if entry is not None and os.path.exists(entry[0]):
                self._audio_cache.move_to_end(pos)
                return entry
            pending = self._generation_tasks.get(pos)

        if pending is not None:
            # Une génération est déjà en cours pour cette position : on la réutilise.
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                raise
            except Exception:
                pending = None

        task = asyncio.create_task(self._generate(pos))
        async with self._cache_lock:
            self._generation_tasks[pos] = task

        try:
            return await task
        finally:
            async with self._cache_lock:
                if self._generation_tasks.get(pos) is task:
                    self._generation_tasks.pop(pos, None)

    async def prefetch_position(self, pos: Tuple[int, int, int]):
        """Pré-génère l'audio pour une position en arrière-plan (fire-and-forget)."""
        try:
            await self.get_or_generate(pos)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.debug(f"Erreur prefetch audio pour position {pos}: {e}")

    async def clear_cache(self):
        """Annule les générations en cours et supprime tous les fichiers du cache de manière non bloquante."""
        async with self._cache_lock:
            for task in self._generation_tasks.values():
                if not task.done():
                    task.cancel()
            self._generation_tasks.clear()

            paths = [entry[0] for entry in self._audio_cache.values() if entry and entry[0]]
            self._audio_cache.clear()

        if paths:
            await asyncio.gather(*[_remove_file_with_retries(p) for p in paths], return_exceptions=True)


async def stop_and_clear_audio(reader: Any) -> None:
    """Stop audio playback, cancel active tasks, and clear the audio queue instantly.

    IMPORTANT : 'reader.running' est le drapeau de vie de l'APPLICATION (les
    boucles UI de reader.py tournent sur 'while self.running'). Il ne doit
    JAMAIS être basculé ici, même temporairement : l'ancienne séquence
    running=False -> (>=50 ms d'attentes) -> running=True tuait définitivement
    _ui_update_loop / _word_update_loop pendant la fenêtre False, ce qui figeait
    la surbrillance du texte dès la première phrase (l'audio continuait, plus
    rien ne recopiait les positions vers l'interface). L'arrêt des boucles
    audio se fait exclusivement par task.cancel(), sans toucher au drapeau.
    """
    tasks_to_cancel = []
    for task in [getattr(reader, 'producer_task', None), getattr(reader, 'player_task', None)]:
        if task and not task.done():
            task.cancel()
            tasks_to_cancel.append(task)

    if tasks_to_cancel:
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

    reader.producer_task = None
    reader.player_task = None

    if hasattr(reader, 'audio_pipeline') and reader.audio_pipeline:
        await reader.audio_pipeline.clear_cache()

    processes_to_kill = reader.playback_processes.copy()
    reader.playback_processes.clear()

    terminate_coros = [_terminate_process(proc) for proc in processes_to_kill]
    if terminate_coros:
        await asyncio.gather(*terminate_coros, return_exceptions=True)

    while not reader.audio_queue.empty():
        try:
            reader.audio_queue.get_nowait()
            reader.audio_queue.task_done()
        except asyncio.QueueEmpty:
            break

    await asyncio.sleep(0.05)


async def get_audio_duration(file_path: str) -> Optional[float]:
    """Get audio duration with a bounded ffprobe call."""
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logging.error("Unable to start ffprobe for %r: %s", file_path, exc)
        return None

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=FFPROBE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logging.error("ffprobe timed out after %.1fs for %r", FFPROBE_TIMEOUT, file_path)
        await _terminate_process(process)
        return None
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise

    if process.returncode != 0:
        return None

    try:
        duration = float(stdout.decode(errors="replace").strip())
    except (ValueError, TypeError):
        return None

    if duration < 0:
        return None

    return duration


async def play_from_current_position(reader: Any) -> None:
    """Start the audio producer and player loops."""
    if not reader.is_paused and reader.running and reader.tts_model:
        for task in [getattr(reader, 'producer_task', None), getattr(reader, 'player_task', None)]:
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        reader.producer_task = None
        reader.player_task = None

        if not hasattr(reader, 'audio_pipeline') or not reader.audio_pipeline:
            reader.audio_pipeline = AudioPipeline(reader)

        await asyncio.sleep(0.05)

        reader.producer_task = asyncio.create_task(_producer_loop(reader))
        reader.player_task = asyncio.create_task(_player_loop(reader))


async def _producer_loop(reader: Any) -> None:
    """Producer loop using the position-keyed cache/prefetch pipeline."""
    if not reader.tts_model or not reader.tts_model.initialized:
        try:
            await asyncio.wait_for(reader.audio_queue.put(None), timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return

    if not hasattr(reader, 'audio_pipeline') or not reader.audio_pipeline:
        reader.audio_pipeline = AudioPipeline(reader)

    producer_pos = (reader.chapter_idx, reader.paragraph_idx, reader.sentence_idx)
    try:
        while reader.running:
            if reader.audio_queue.qsize() >= config.MAX_QUEUE_SIZE - 1:
                await asyncio.sleep(0.05)
                continue

            entry = await reader.audio_pipeline.get_or_generate(producer_pos)
            if entry is None:
                # Phrase vide ou génération impossible : on avance sans bloquer la lecture.
                next_pos = reader._advance_position(producer_pos, wrap=False)
                if not next_pos:
                    break
                producer_pos = next_pos
                continue

            audio_file, duration, timing_info = entry

            if not reader.running:
                break

            # Injection dans la queue principale
            try:
                await asyncio.wait_for(
                    reader.audio_queue.put((audio_file, *producer_pos, duration, timing_info)),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                break

            # Calcul de la position suivante et déclenchement du prefetch
            next_pos = reader._advance_position(producer_pos, wrap=False)
            if next_pos:
                asyncio.create_task(reader.audio_pipeline.prefetch_position(next_pos))
                producer_pos = next_pos
            else:
                break

    except asyncio.CancelledError:
        pass
    finally:
        try:
            await asyncio.wait_for(reader.audio_queue.put(None), timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


async def _player_loop(reader: Any) -> None:
    """Player loop to play audio files using ffplay."""
    try:
        while reader.running:
            try:
                item = await asyncio.wait_for(reader.audio_queue.get(), timeout=1.0)
                if item is None:
                    reader.audio_queue.task_done()
                    if reader.active_playback_tasks:
                        await asyncio.gather(*reader.active_playback_tasks, return_exceptions=True)
                    reader.playback_finished_event.set()
                    break

                audio_file, c, p, s, duration, timing_data = item
                if isinstance(timing_data, dict):
                    timing_info = timing_data
                else:
                    timing_info = {"word_timings": timing_data, "speech_duration": duration, "total_duration": duration}

                if not audio_file or not os.path.exists(audio_file) or duration is None or duration <= 0:
                    try:
                        reader.loop.call_soon_threadsafe(
                            reader._post_command_sync,
                            ('_new_sentence_started', (c, p, s, max(0.0, duration or 0.0), timing_info))
                        )
                    except RuntimeError:
                        reader.audio_queue.task_done()
                        break
                    reader.audio_queue.task_done()
                    continue

                try:
                    reader.loop.call_soon_threadsafe(
                        reader._post_command_sync,
                        ('_new_sentence_started', (c, p, s, duration, timing_info))
                    )
                except RuntimeError:
                    reader.audio_queue.task_done()
                    break

                try:
                    cmd = ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'error']

                    if abs(reader.playback_speed - 1.0) > 0.01:
                        speed = reader.playback_speed
                        filters = []
                        while speed > 2.0:
                            filters.append('atempo=2.0')
                            speed /= 2.0
                        while speed < 0.5:
                            filters.append('atempo=0.5')
                            speed /= 0.5
                        if abs(speed - 1.0) > 0.01:
                            filters.append(f'atempo={speed:.3f}')
                        if filters:
                            cmd.extend(['-af', ','.join(filters)])

                    cmd.append(audio_file)
                    process = await asyncio.create_subprocess_exec(
                        *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    reader.playback_processes.append(process)
                except (OSError, ValueError) as exc:
                    logging.error("Unable to start ffplay for %r: %s", audio_file, exc)
                    reader.audio_queue.task_done()
                    continue

                # Calcul du délai avec gestion propre de l'overlap
                base_overlap = config.OVERLAP_SECONDS
                if reader.tts_model and hasattr(reader.tts_model, 'get_overlap_seconds'):
                    tts_overlap = reader.tts_model.get_overlap_seconds()
                    if tts_overlap is not None:
                        base_overlap = tts_overlap

                speed = reader.playback_speed
                if speed >= 3.0:
                    overlap_seconds = 0.0
                else:
                    overlap_factor = max(0.0, min(1.0, (3.0 - speed) / 2.0))
                    overlap_seconds = base_overlap * overlap_factor

                actual_duration = duration / speed
                sleep_duration = max(0.1, actual_duration - overlap_seconds)

                # Attente non bloquante de la fin de la phrase avec libération garantie de la queue
                try:
                    await asyncio.sleep(sleep_duration)
                except asyncio.CancelledError:
                    await _terminate_process(process)
                    raise

                reader.audio_queue.task_done()

            except asyncio.TimeoutError:
                if not reader.running:
                    break
                continue
            except asyncio.CancelledError:
                break
    except asyncio.CancelledError:
        pass
    finally:
        for process in reader.playback_processes.copy():
            try:
                if process.returncode is None:
                    process.terminate()
            except (ProcessLookupError, AttributeError):
                pass