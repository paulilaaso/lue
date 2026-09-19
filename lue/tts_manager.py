"""TTS model discovery and management for the Lue eBook reader."""

import importlib
import inspect
import logging
from pathlib import Path
from rich.console import Console
from .tts.base import TTSBase
from . import config


class TTSManager:
    """
    Discovers, loads, and manages available TTS models.
    
    This class automatically discovers TTS models in the tts/ directory
    and provides a unified interface for creating model instances.
    """

    def __init__(self):
        """Initialize the TTS manager and discover available models."""
        self._models = {}
        self._instances = {}  # Cache pour éviter les ré-instanciations inutiles
        self._discover_models()

    def _discover_models(self):
        """
        Dynamically discover TTS models from the tts/ directory.
        
        Looks for Python files matching the pattern '*_tts.py' and attempts
        to load classes that inherit from TTSBase.
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
        """
        Get a list of available TTS model names.
        
        Returns:
            list[str]: Sorted list of model names, with the default model first.
        """
        names = sorted(self._models.keys())
        # Prioritize the default TTS model
        default_model = get_default_tts_model_name(names)
        if default_model in names:
            names.remove(default_model)
            names.insert(0, default_model)
        return names

    def create_model(self, name: str, console: Console, voice: str = None, lang: str = None) -> TTSBase | None:
        """
        Create or retrieve an instance of the specified TTS model.
        
        Args:
            name: Name of the TTS model to create
            console: Rich console instance for user feedback
            voice: Optional voice for the TTS model
            lang: Optional language for the TTS model
            
        Returns:
            TTSBase: Model instance, or None if model not found or failed to initialize
        """
        # Utilisation d'une clé de cache basée sur les paramètres pour réutiliser l'instance si elle existe
        cache_key = f"{name}:{voice}:{lang}"
        if cache_key in self._instances:
            return self._instances[cache_key]

        model_class = self._models.get(name)
        if model_class:
            try:
                instance = model_class(console, voice=voice, lang=lang)
                self._instances[cache_key] = instance
                return instance
            except Exception as e:
                logging.error(f"Failed to instantiate TTS model '{name}': {e}", exc_info=True)
                return None

        logging.error(f"TTS model '{name}' not found.")
        return None


def get_default_tts_model_name(available_models: list[str]) -> str:
    """
    Determine the default TTS model name from the available list.
    
    Uses the model specified in config.py, falling back to the first available
    model if the configured one is not found.
    
    Args:
        available_models: List of available model names
        
    Returns:
        str: Name of the default TTS model
    """
    if getattr(config, "DEFAULT_TTS_MODEL", None) in available_models:
        return config.DEFAULT_TTS_MODEL
    return available_models[0] if available_models else ""
