"""TTS model discovery and management for the Lue eBook reader."""

import importlib
import inspect
import logging
import threading
from pathlib import Path
from rich.console import Console
from .tts.base import TTSBase
from . import config


class TTSManager:
    """
    Discovers, loads, and manages available TTS models in a thread-safe manner.
    """
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Garantit un pattern Singleton thread-safe pour le manager lui-même."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the TTS manager and discover available models (thread-safe check)."""
        # Évite de réinitialiser si le singleton existe déjà
        if hasattr(self, "_initialized") and self._initialized:
            return
            
        with self._lock:
            if getattr(self, "_initialized", False):
                return
                
            self._models = {}
            self._instances = {}  # Cache thread-safe pour les instances de modèles TTS
            self._discover_models()
            self._initialized = True

    def _discover_models(self):
        """
        Dynamically discover TTS models from the tts/ directory.
        """
        tts_dir = Path(__file__).parent / "tts"
        if not tts_dir.exists():
            logging.warning(f"Le dossier TTS '{tts_dir}' est introuvable.")
            return

        for file_path in tts_dir.glob("*_tts.py"):
            module_name = file_path.stem
            try:
                module = importlib.import_module(f".tts.{module_name}", package="lue")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if (issubclass(obj, TTSBase) and 
                        not inspect.isabstract(obj) and 
                        obj is not TTSBase):
                        model_name = module_name.replace("_tts", "")
                        self._models[model_name] = obj
                        logging.info(f"Discovered TTS model: {model_name}")
                        break
            except Exception as e:
                logging.error(f"Failed to load TTS module {module_name}: {e}", exc_info=True)

    def get_available_tts_names(self) -> list[str]:
        """Get a list of available TTS model names."""
        with self._lock:
            names = sorted(self._models.keys())
        default_model = get_default_tts_model_name(names)
        if default_model in names:
            names.remove(default_model)
            names.insert(0, default_model)
        return names

    def create_model(self, name: str, console: Console, voice: str = None, lang: str = None) -> TTSBase | None:
        """
        Create or retrieve an instance of the specified TTS model safely across threads.
        """
        cache_key = f"{name}:{voice}:{lang}"
        
        # Double-checked locking pattern pour l'accès au cache des modèles
        with self._lock:
            if cache_key in self._instances:
                return self._instances[cache_key]

            model_class = self._models.get(name)
            if not model_class:
                logging.error(f"TTS model '{name}' not found.")
                return None

        try:
            # L'instanciation externe se fait hors du verrou lourd si nécessaire, 
            # mais on sécurise l'écriture dans le dictionnaire _instances
            instance = model_class(console, voice=voice, lang=lang)
            with self._lock:
                self._instances[cache_key] = instance
            return instance
        except Exception as e:
            logging.error(f"Failed to instantiate TTS model '{name}': {e}", exc_info=True)
            return None


def get_default_tts_model_name(available_models: list[str]) -> str:
    """Determine the default TTS model name from the available list."""
    if getattr(config, "DEFAULT_TTS_MODEL", None) in available_models:
        return config.DEFAULT_TTS_MODEL
    return available_models[0] if available_models else ""
