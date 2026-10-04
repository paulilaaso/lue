"""
Reading progress management for the Lue eBook reader.
(Backward Compatible with Session Tracking & Statistics)
"""

import os
import json
import re
import glob
import tempfile
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from . import config

# Compile regex pattern once for better performance
COMPILED_SAFE_TITLE_REGEX = re.compile(r'[^A-Za-z0-9]+')

# Default extended progress configuration dictionary
DEFAULT_PROGRESS: Dict[str, Any] = {
    "c": 0,
    "p": 0,
    "s": 0,
    "scroll_offset": 0.0,
    "tts_enabled": True,
    "auto_scroll_enabled": True,
    "speed_reading_enabled": False,
    "manual_scroll_anchor": None,
    "playback_speed": 1.0,
    "completion_percentage": 0.0,
    "sessions": [],
    "total_reading_time": 0.0,
    "average_reading_speed": 250.0,
    "last_read": None,
    "first_read": None,
}

@dataclass
class ReadingSession:
    """Représente une session de lecture unique."""
    date: str
    start_time: str
    end_time: Optional[str] = None
    start_pos: Dict[str, int] = field(default_factory=lambda: {"c": 0, "p": 0, "s": 0})
    end_pos: Dict[str, int] = field(default_factory=lambda: {"c": 0, "p": 0, "s": 0})
    duration_minutes: float = 0.0
    words_read: int = 0
    reading_speed: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProgressManager:
    """Gestionnaire de progression enrichi avec suivi de session et compatibilité totale."""
    
    def __init__(self, progress_dir: Optional[str] = None):
        self.progress_dir = progress_dir or getattr(config, 'PROGRESS_FILE_DIR', os.path.expanduser("~/.lue"))
        os.makedirs(self.progress_dir, exist_ok=True)
        self.current_session: Optional[ReadingSession] = None
    
    def get_progress_file_path(self, book_title: str) -> str:
        safe_title = COMPILED_SAFE_TITLE_REGEX.sub('', book_title)
        return os.path.join(self.progress_dir, f"{safe_title}.progress.json")
    
    def _atomic_write_json(self, file_path: str, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        dir_name = os.path.dirname(file_path)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, file_path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise
    
    def load_progress(self, progress_file: str) -> Tuple[int, int, int]:
        if not os.path.exists(progress_file):
            return 0, 0, 0
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("c", 0), data.get("p", 0), data.get("s", 0)
        except (json.JSONDecodeError, IOError):
            return 0, 0, 0
    
    def load_extended_progress(self, progress_file: str) -> Dict[str, Any]:
        if not os.path.exists(progress_file):
            return DEFAULT_PROGRESS.copy()
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            result = DEFAULT_PROGRESS.copy()
            for key in result:
                if key in data:
                    result[key] = data[key]
            if "original_file_path" in data:
                result["original_file_path"] = data["original_file_path"]
            return result
        except (json.JSONDecodeError, IOError):
            return DEFAULT_PROGRESS.copy()
    
    def save_progress(self, progress_file: str, chapter_idx: int, paragraph_idx: int, sentence_idx: int) -> None:
        progress = {"c": chapter_idx, "p": paragraph_idx, "s": sentence_idx}
        self._atomic_write_json(progress_file, progress)
    
    def save_extended_progress(
        self, progress_file: str, chapter_idx: int, paragraph_idx: int, sentence_idx: int, 
        scroll_offset: float, tts_enabled: bool, auto_scroll_enabled: bool, 
        manual_scroll_anchor: Optional[Any] = None, original_file_path: Optional[str] = None, 
        playback_speed: float = 1.0, percentage: float = 0.0, speed_reading_enabled: bool = False,
        **kwargs: Any
    ) -> None:
        # Charger l'existant pour préserver les sessions et statistiques déjà enregistrées
        existing_data = self.load_extended_progress(progress_file)
        
        progress = {
            "c": chapter_idx,
            "p": paragraph_idx, 
            "s": sentence_idx,
            "scroll_offset": float(scroll_offset),
            "tts_enabled": bool(tts_enabled),
            "auto_scroll_enabled": bool(auto_scroll_enabled),
            "speed_reading_enabled": bool(speed_reading_enabled),
            "playback_speed": float(playback_speed),
            "completion_percentage": float(percentage),
            "sessions": existing_data.get("sessions", []),
            "total_reading_time": existing_data.get("total_reading_time", 0.0),
            "average_reading_speed": existing_data.get("average_reading_speed", 250.0),
            "last_read": datetime.now().isoformat(),
            "first_read": existing_data.get("first_read") or datetime.now().isoformat()
        }
        
        if manual_scroll_anchor is not None:
            progress["manual_scroll_anchor"] = manual_scroll_anchor
        if original_file_path is not None:
            progress["original_file_path"] = original_file_path
            
        # Fusionner d'éventuels arguments additionnels de session
        progress.update(kwargs)
        
        self._atomic_write_json(progress_file, progress)

    def get_recent_books(self, limit: int = 5) -> List[Dict[str, Any]]:
        progress_files = glob.glob(os.path.join(self.progress_dir, "*.progress.json"))
        valid_files = []
        for pf in progress_files:
            try:
                valid_files.append((pf, os.path.getmtime(pf)))
            except OSError:
                continue
        valid_files.sort(key=lambda x: x[1], reverse=True)
        
        recent_books = []
        for pf, _ in valid_files:
            if len(recent_books) >= limit:
                break
            try:
                with open(pf, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                original_path = data.get("original_file_path")
                if not original_path or not os.path.exists(original_path):
                    continue
                recent_books.append({
                    "title": os.path.splitext(os.path.basename(original_path))[0],
                    "path": original_path,
                    "percentage": data.get("completion_percentage", 0.0)
                })
            except (json.JSONDecodeError, IOError):
                continue
        return recent_books

    def validate_and_set_progress(self, chapters: List[List[str]], progress_file: str, c: int, p: int, s: int) -> Tuple[int, int, int]:
        try:
            paragraph = chapters[c][p]
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            _ = sentences[s]
            return c, p, s
        except (IndexError, TypeError):
            if os.path.exists(progress_file):
                try:
                    os.remove(progress_file)
                except OSError:
                    pass
            return 0, 0, 0

    def find_most_recent_book(self) -> Optional[str]:
        progress_files = glob.glob(os.path.join(self.progress_dir, "*.progress.json"))
        if not progress_files:
            return None
        valid_files = []
        for pf in progress_files:
            try:
                valid_files.append((pf, os.path.getmtime(pf)))
            except OSError:
                continue
        if not valid_files:
            return None
        most_recent_file = max(valid_files, key=lambda x: x[1])[0]
        try:
            with open(most_recent_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                original_path = data.get("original_file_path")
                if original_path and os.path.exists(original_path):
                    return original_path
        except (json.JSONDecodeError, IOError):
            pass
        return None


# --- INSTANCE GLOBALE ET FONCTIONS PASSERELLES (RÉGRESSION ZÉRO) ---
_default_manager: Optional[ProgressManager] = None

def _get_manager() -> ProgressManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = ProgressManager()
    return _default_manager

def get_progress_file_path(book_title: str) -> str:
    return _get_manager().get_progress_file_path(book_title)

def load_progress(progress_file: str) -> Tuple[int, int, int]:
    return _get_manager().load_progress(progress_file)

def load_extended_progress(progress_file: str) -> Dict[str, Any]:
    return _get_manager().load_extended_progress(progress_file)

def save_progress(progress_file: str, chapter_idx: int, paragraph_idx: int, sentence_idx: int) -> None:
    return _get_manager().save_progress(progress_file, chapter_idx, paragraph_idx, sentence_idx)

def save_extended_progress(
    progress_file: str, chapter_idx: int, paragraph_idx: int, sentence_idx: int, 
    scroll_offset: float, tts_enabled: bool, auto_scroll_enabled: bool, 
    manual_scroll_anchor: Optional[Any] = None, original_file_path: Optional[str] = None, 
    playback_speed: float = 1.0, percentage: float = 0.0, speed_reading_enabled: bool = False,
    **kwargs: Any
) -> None:
    return _get_manager().save_extended_progress(
        progress_file, chapter_idx, paragraph_idx, sentence_idx, 
        scroll_offset, tts_enabled, auto_scroll_enabled, 
        manual_scroll_anchor, original_file_path, playback_speed, percentage, speed_reading_enabled, **kwargs
    )

def get_recent_books(limit: int = 5) -> List[Dict[str, Any]]:
    return _get_manager().get_recent_books(limit)

def validate_and_set_progress(chapters: List[List[str]], progress_file: str, c: int, p: int, s: int) -> Tuple[int, int, int]:
    return _get_manager().validate_and_set_progress(chapters, progress_file, c, p, s)

def find_most_recent_book() -> Optional[str]:
    return _get_manager().find_most_recent_book()