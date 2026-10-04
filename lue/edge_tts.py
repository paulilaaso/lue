"""
TTS implementation for Microsoft Edge's online TTS service.
Robustness improvements: non-blocking stream write, safe cache re-use, 
and unique temporary file management during warm-up.
"""

import asyncio
import logging
import os
import uuid
from typing import List, Tuple, Any, Optional
from rich.console import Console

from .base import TTSBase
from .. import config

# Conversion factor from 100-nanosecond units to seconds
_NS_TO_SECONDS = 10_000_000.0


class EdgeTTS(TTSBase):
    """TTS implementation for Microsoft Edge's online TTS service."""

    timings_include_audio = True  # get_raw_timing_data() écrit aussi le MP3

    @property
    def name(self) -> str:
        return "edge"

    @property
    def output_format(self) -> str:
        return "mp3"

    def __init__(self, console: Console, voice: Optional[str] = None, lang: Optional[str] = None) -> None:
        super().__init__(console, voice, lang)
        self.edge_tts: Any = None
        if self.voice is None:
            self.voice = config.TTS_VOICES.get(self.name)
        if self.lang is None:
            self.lang = config.TTS_LANGUAGE_CODES.get(self.name)

    async def initialize(self) -> bool:
        """Checks if the edge-tts library is available."""
        try:
            import edge_tts
            self.edge_tts = edge_tts
            self.initialized = True
            self.console.print("[green]Edge TTS model is available.[/green]")
            return True
        except ImportError:
            self.console.print("[bold red]Error: 'edge-tts' package not found.[/bold red]")
            self.console.print("[yellow]Please run 'pip install edge-tts' to use this TTS model.[/yellow]")
            logging.error("'edge-tts' is not installed.")
            return False

    async def get_raw_timing_data(self, text: str, output_path: str) -> List[Tuple[str, float, float]]:
        """
        Get raw word timing data from Edge TTS and stream audio directly to file.
        
        Returns:
            List of (word, start_time, end_time) tuples with raw timing data from Edge TTS
        """
        if not self.initialized:
            raise RuntimeError("Edge TTS has not been initialized.")
        
        try:
            communicate = self.edge_tts.Communicate(text, self.voice, boundary="WordBoundary")
            word_timings = []
            
            target_dir = os.path.dirname(os.path.abspath(output_path))
            await asyncio.to_thread(os.makedirs, target_dir, exist_ok=True)
            
            # Ouvre le fichier de manière synchrone
            with open(output_path, 'wb') as f:
                # La boucle async for reste dans la méthode async principale
                async for chunk in communicate.stream():
                    chunk_type = chunk.get('type')
                    if chunk_type == 'WordBoundary':
                        start_time = chunk['offset'] / _NS_TO_SECONDS
                        end_time = (chunk['offset'] + chunk['duration']) / _NS_TO_SECONDS
                        word_timings.append((chunk['text'], start_time, end_time))
                    elif chunk_type == 'audio':
                        # Seule l'écriture sur disque est envoyée dans un thread pour ne pas bloquer la boucle
                        await asyncio.to_thread(f.write, chunk['data'])
            
            return word_timings
            
        except Exception as e:
            logging.error(f"Edge TTS audio generation failed for text: '{text[:50]}...'", exc_info=True)
            if await asyncio.to_thread(os.path.exists, output_path):
                try:
                    await asyncio.to_thread(os.remove, output_path)
                except OSError:
                    pass
            raise e

    async def generate_audio(self, text: str, output_path: str) -> None:
        """Generates audio from text using edge-tts and saves it to a file."""
        if not self.initialized:
            raise RuntimeError("Edge TTS has not been initialized.")
        try:
            communicate = self.edge_tts.Communicate(text, self.voice)
            target_dir = os.path.dirname(os.path.abspath(output_path))
            await asyncio.to_thread(os.makedirs, target_dir, exist_ok=True)
            await communicate.save(output_path)
        except Exception as e:
            logging.error(f"Edge TTS audio generation failed for text: '{text[:50]}...'", exc_info=True)
            if await asyncio.to_thread(os.path.exists, output_path):
                try:
                    await asyncio.to_thread(os.remove, output_path)
                except OSError:
                    pass
            raise e

    async def warm_up(self) -> None:
        """Warms up the TTS model by making a short request."""
        if not self.initialized:
            return

        self.console.print("[bold cyan]Warming up the Edge TTS model...[/bold cyan]")
        
        unique_id = uuid.uuid4().hex[:8]
        warmup_file = os.path.join(config.AUDIO_DATA_DIR, f".warmup_edge_{os.getpid()}_{unique_id}.{self.output_format}")
        
        try:
            await self.generate_audio("Ready.", warmup_file)
            self.console.print("[green]Edge TTS model is ready.[/green]")
        except Exception as e:
            self.console.print("[bold yellow]Warning: Edge model warm-up failed.[/bold yellow]")
            self.console.print(f"[yellow]This may indicate a network issue or an invalid voice name: {self.voice}[/yellow]")
            logging.warning(f"Edge TTS model warm-up failed: {e}", exc_info=True)
        finally:
            if await asyncio.to_thread(os.path.exists, warmup_file):
                try:
                    await asyncio.to_thread(os.remove, warmup_file)
                except OSError:
                    pass