import os
import platform
import warnings
import asyncio
import logging
import re
from rich.console import Console

from .base import TTSBase
from .. import config

warnings.filterwarnings("ignore")
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "10"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "10"


class KokoroTTS(TTSBase):
    """TTS implementation for Kokoro TTS."""

    timings_include_audio = True

    @property
    def name(self) -> str:
        return "kokoro"
    
    @property
    def output_format(self) -> str:
        return "wav"

    def __init__(self, console: Console, voice: str = None, lang: str = None):
        super().__init__(console, voice, lang)
        self.pipeline = None
        self.np = None
        self.sf = None
        
        if self.voice is None:
            self.voice = config.TTS_VOICES.get(self.name)
        
        if self.lang is None:
            self.lang = config.TTS_LANGUAGE_CODES.get(self.name)

    def _patch_hf_downloader(self):
        """Patches hf_hub_download to show download progress messages."""
        try:
            if hasattr(self.huggingface_hub, "_patched_by_lue"):
                return
            
            original_hf_hub_download = self.huggingface_hub.hf_hub_download
            
            def tracked_hf_hub_download(*args, **kwargs):
                try:
                    local_kwargs = dict(kwargs)
                    local_kwargs["local_files_only"] = True
                    original_hf_hub_download(*args, **local_kwargs)
                except Exception:
                    repo_id = kwargs.get("repo_id", "<unknown repo>")
                    filename = kwargs.get("filename", "<unknown file>")
                    msg = f"[bold yellow]Downloading model '{filename}' from Hugging Face ({repo_id}). This may take a while...[/bold yellow]"
                    self.console.print(msg)
                return original_hf_hub_download(*args, **kwargs)

            self.huggingface_hub.hf_hub_download = tracked_hf_hub_download
            self.huggingface_hub._patched_by_lue = True
        except Exception as e:
            logging.warning(f"Failed to patch Hugging Face downloader: {e}")

    async def initialize(self) -> bool:
        """Initializes the Kokoro TTS pipeline asynchronously."""
        try:
            import numpy
            import soundfile as sf
            from kokoro import KPipeline
            import huggingface_hub
            
            self.np = numpy
            self.sf = sf
            self.KPipeline = KPipeline
            self.huggingface_hub = huggingface_hub
        except SystemExit:
            self.console.print("[bold red]Error: The TTS library exited unexpectedly during import.[/bold red]")
            self.console.print("[yellow]This can happen if a required dependency is missing or misconfigured.[/yellow]")
            logging.error("SystemExit was called during Kokoro TTS import.")
            return False
        except ImportError as e:
            package = str(e).split("'")[1]
            self.console.print(f"[bold red]Error: '{package}' package not found.[/bold red]")
            self.console.print(f"[yellow]Please ensure torch, kokoro, soundfile, etc. are installed to use this TTS model.[/yellow]")
            logging.error(f"'{package}' is not installed for Kokoro TTS.")
            return False

        self._patch_hf_downloader()
        loop = asyncio.get_running_loop()

        def _blocking_init():
            gpu_msg, use_gpu = self._get_gpu_acceleration()
            pipeline, error_msg, device_used = None, None, None
            
            if use_gpu:
                device_to_try = "mps" if platform.system() == "Darwin" else "cuda"
                try:
                    pipeline = self.KPipeline(repo_id="hexgrad/Kokoro-82M", device=device_to_try, lang_code=self.lang)
                    device_used = device_to_try
                except Exception as gpu_error:
                    error_msg = f"Failed to initialize on GPU ({device_to_try}): {gpu_error}"
            
            if pipeline is None:
                try:
                    pipeline = self.KPipeline(repo_id="hexgrad/Kokoro-82M", device="cpu", lang_code=self.lang)
                    device_used = "cpu"
                except Exception as cpu_error:
                    error_msg = f"Failed to initialize on CPU: {cpu_error}"
            
            return pipeline, (gpu_msg, error_msg), device_used

        try:
            pipeline, (gpu_msg, error_details), device_used = await loop.run_in_executor(None, _blocking_init)
            self.console.print(f"[cyan]GPU Check: {gpu_msg}[/cyan]")
            
            if pipeline:
                self.pipeline = pipeline
                self.console.print(f"[green]Kokoro TTS model initialized successfully on {device_used}.[/green]")
                self.initialized = True
                return True
            else:
                self.console.print(f"[bold red]Kokoro initialization failed.[/bold red]")
                if error_details:
                    self.console.print(f"[red]Error details: {error_details}[/red]")
                    logging.error(f"Kokoro initialization failed: {error_details}")
                return False
        except Exception as e:
            self.console.print(f"[bold red]An unexpected error occurred during Kokoro's async initialization: {e}[/bold red]")
            logging.error("Kokoro async initialization failed.", exc_info=True)
            return False

    async def warm_up(self):
        """Performs a short TTS generation to load the model into memory."""
        if not self.initialized:
            return
        
        self.console.print("[bold cyan]Warming up the Kokoro TTS model... (this may take a minute)[/bold cyan]")
        warmup_file = os.path.join(config.AUDIO_DATA_DIR, f".warmup_kokoro.{self.output_format}")
        
        try:
            await self.generate_audio("Ready.", warmup_file)
            self.console.print("[green]Kokoro TTS model is ready.[/green]")
        except Exception as e:
            self.console.print(f"[bold yellow]Warning: Kokoro model warm-up failed.[/bold yellow]")
            logging.warning(f"Kokoro TTS warm-up failed: {e}", exc_info=True)
        finally:
            if os.path.exists(warmup_file):
                try:
                    os.remove(warmup_file)
                except OSError:
                    pass

    def _get_gpu_acceleration(self):
        """Checks for available GPU acceleration."""
        try:
            import torch
            if torch.cuda.is_available():
                return "NVIDIA CUDA GPU available.", True
            if torch.backends.mps.is_available() and platform.system() == "Darwin":
                return "Apple Metal (MPS) GPU available.", True
            return "No compatible GPU found. Using CPU.", False
        except ImportError:
            return "PyTorch not found. Using CPU.", False
        except Exception as e:
            return f"Error checking for GPU ({e}). Using CPU.", False

    async def get_raw_timing_data(self, text: str, output_path: str):
        """
        Get raw word timing data from Kokoro TTS.
        
        Returns:
            List of (word, start_time, end_time) tuples with raw timing data from Kokoro TTS
        """
        if not self.initialized or not self.pipeline:
            raise RuntimeError("Kokoro TTS has not been initialized.")
        
        def _blocking_generate():
            try:
                # Generate audio with timing information
                results = list(self.pipeline(text, voice=self.voice, split_pattern=None))
                
                if results:
                    # Concatenate all audio segments
                    audio_segments = [result.audio for result in results]
                    full_audio = self.np.concatenate(audio_segments)
                    self.sf.write(output_path, full_audio, 24000)
                    
                    # Extract precise timing information from tokens
                    word_timings = []
                    
                    # Process each result to extract word-level timing
                    for result in results:
                        if hasattr(result, 'tokens') and result.tokens:
                            # Extract timing from tokens
                            for token in result.tokens:
                                # Skip punctuation tokens for word timing
                                if token.tag in ['.', ',', '!', '?', ':', ';']:
                                    continue
                                
                                # Use the actual text and timing from the token
                                word = token.text
                                start_time = token.start_ts
                                end_time = token.end_ts
                                
                                # Filter out None values which can cause errors in timing calculations
                                if start_time is not None and end_time is not None:
                                    # Only include tokens that contain alphanumeric characters
                                    # This ensures consistency with the timing calculator and UI
                                    if re.search(r'[a-zA-Z0-9]', word):
                                        word_timings.append((word, start_time, end_time))
                    
                    return word_timings
                else:
                    self.sf.write(output_path, self.np.array([], dtype=self.np.float32), 24000)
                    return []
            except Exception as e:
                logging.error(f"Error during Kokoro audio generation for text '{text[:50]}...': {e}", exc_info=True)
                raise e
        
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _blocking_generate)

    async def generate_audio(self, text: str, output_path: str):
        """Generates audio from text using Kokoro in a separate thread."""
        if not self.initialized or not self.pipeline:
            raise RuntimeError("Kokoro TTS has not been initialized.")
        
        def _blocking_generate():
            try:
                audio_segments = [result.audio for result in self.pipeline(text, voice=self.voice, split_pattern=None)]
                if audio_segments:
                    full_audio = self.np.concatenate(audio_segments)
                    self.sf.write(output_path, full_audio, 24000)
                else:
                    self.sf.write(output_path, self.np.array([], dtype=self.np.float32), 24000)
            except Exception as e:
                logging.error(f"Error during Kokoro audio generation for text '{text[:50]}...': {e}", exc_info=True)
                raise e
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _blocking_generate)