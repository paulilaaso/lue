"""
Reading progress management for the Lue eBook reader.
"""

import os
import json
import re
import glob
from typing import Dict, Any, List, Optional, Tuple
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
    "completion_percentage": 0.0
}


def get_progress_file_path(book_title: str) -> str:
    """
    Generate the file path for storing reading progress.
    
    Args:
        book_title: Title of the book
        
    Returns:
        str: Full path to the progress file
    """
    safe_title = COMPILED_SAFE_TITLE_REGEX.sub('', book_title)
    return os.path.join(config.PROGRESS_FILE_DIR, f"{safe_title}.progress.json")


def load_progress(progress_file: str) -> Tuple[int, int, int]:
    """
    Load basic reading progress from file.
    
    Args:
        progress_file: Path to the progress file
        
    Returns:
        tuple: (chapter_idx, paragraph_idx, sentence_idx)
    """
    if not os.path.exists(progress_file):
        return 0, 0, 0
        
    try:
        with open(progress_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("c", 0), data.get("p", 0), data.get("s", 0)
    except (json.JSONDecodeError, IOError):
        return 0, 0, 0


def load_extended_progress(progress_file: str) -> Dict[str, Any]:
    """
    Load extended reading progress including UI state.
    
    Args:
        progress_file: Path to the progress file
        
    Returns:
        dict: Progress data with reading position and UI state
    """
    if not os.path.exists(progress_file):
        return DEFAULT_PROGRESS.copy()
        
    try:
        with open(progress_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {
                "c": data.get("c", DEFAULT_PROGRESS["c"]),
                "p": data.get("p", DEFAULT_PROGRESS["p"]), 
                "s": data.get("s", DEFAULT_PROGRESS["s"]),
                "scroll_offset": data.get("scroll_offset", DEFAULT_PROGRESS["scroll_offset"]),
                "tts_enabled": data.get("tts_enabled", DEFAULT_PROGRESS["tts_enabled"]),
                "auto_scroll_enabled": data.get("auto_scroll_enabled", DEFAULT_PROGRESS["auto_scroll_enabled"]),
                "speed_reading_enabled": data.get("speed_reading_enabled", DEFAULT_PROGRESS["speed_reading_enabled"]),
                "manual_scroll_anchor": data.get("manual_scroll_anchor", DEFAULT_PROGRESS["manual_scroll_anchor"]),
                "playback_speed": data.get("playback_speed", DEFAULT_PROGRESS["playback_speed"]),
                "completion_percentage": data.get("completion_percentage", DEFAULT_PROGRESS["completion_percentage"])
            }
    except (json.JSONDecodeError, IOError):
        return DEFAULT_PROGRESS.copy()


def save_progress(progress_file: str, chapter_idx: int, paragraph_idx: int, sentence_idx: int) -> None:
    """
    Save basic reading progress to file.
    
    Args:
        progress_file: Path to the progress file
        chapter_idx: Current chapter index
        paragraph_idx: Current paragraph index
        sentence_idx: Current sentence index
    """
    progress = {"c": chapter_idx, "p": paragraph_idx, "s": sentence_idx}
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)


def save_extended_progress(
    progress_file: str, 
    chapter_idx: int, 
    paragraph_idx: int, 
    sentence_idx: int, 
    scroll_offset: float, 
    tts_enabled: bool, 
    auto_scroll_enabled: bool, 
    manual_scroll_anchor: Optional[Any] = None, 
    original_file_path: Optional[str] = None, 
    playback_speed: float = 1.0, 
    percentage: float = 0.0, 
    speed_reading_enabled: bool = False
) -> None:
    """
    Save extended reading progress including UI state.
    
    Args:
        progress_file: Path to the progress file
        chapter_idx: Current chapter index
        paragraph_idx: Current paragraph index
        sentence_idx: Current sentence index
        scroll_offset: Current scroll position
        tts_enabled: Whether TTS is enabled
        auto_scroll_enabled: Whether auto-scroll is enabled
        manual_scroll_anchor: Manual scroll anchor position (optional)
        original_file_path: Original path to the eBook file (optional)
        playback_speed: Audio playback speed
        percentage: Completion percentage (0.0 to 100.0)
        speed_reading_enabled: Whether speed reading mode is enabled
    """
    progress = {
        "c": chapter_idx,
        "p": paragraph_idx, 
        "s": sentence_idx,
        "scroll_offset": float(scroll_offset),
        "tts_enabled": bool(tts_enabled),
        "auto_scroll_enabled": bool(auto_scroll_enabled),
        "speed_reading_enabled": bool(speed_reading_enabled),
        "playback_speed": float(playback_speed),
        "completion_percentage": float(percentage)
    }
    
    if manual_scroll_anchor is not None:
        progress["manual_scroll_anchor"] = manual_scroll_anchor
    if original_file_path is not None:
        progress["original_file_path"] = original_file_path
        
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)


def get_recent_books(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Get a list of recently read books.
    
    Args:
        limit: Maximum number of books to return
        
    Returns:
        list: List of dicts containing title, path, and percentage
    """
    progress_files = glob.glob(os.path.join(config.PROGRESS_FILE_DIR, "*.progress.json"))
    
    # Sort by modification time (newest first)
    progress_files.sort(key=os.path.getmtime, reverse=True)
    
    recent_books = []
    for pf in progress_files:
        if len(recent_books) >= limit:
            break
            
        try:
            with open(pf, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            original_path = data.get("original_file_path")
            if not original_path or not os.path.exists(original_path):
                continue
                
            title = os.path.splitext(os.path.basename(original_path))[0]
            percentage = data.get("completion_percentage", 0.0)
            
            recent_books.append({
                "title": title,
                "path": original_path,
                "percentage": percentage
            })
            
        except (json.JSONDecodeError, IOError):
            continue
            
    return recent_books


def validate_and_set_progress(chapters: List[List[str]], progress_file: str, c: int, p: int, s: int) -> Tuple[int, int, int]:
    """
    Validate reading progress against document structure.
    
    Args:
        chapters: Document chapters structure
        progress_file: Path to progress file (for cleanup if invalid)
        c: Chapter index to validate
        p: Paragraph index to validate
        s: Sentence index to validate
        
    Returns:
        tuple: Valid (chapter_idx, paragraph_idx, sentence_idx)
    """
    try:
        paragraph = chapters[c][p]
        sentences = re.split(r'(?<=[.!?])\s+', paragraph)
        _ = sentences[s]  # Test if sentence exists
        return c, p, s
    except (IndexError, TypeError):
        if os.path.exists(progress_file):
            try:
                os.remove(progress_file)
            except OSError:
                pass
        return 0, 0, 0


def find_most_recent_book() -> Optional[str]:
    """
    Find the most recently updated progress file and return the original file path.
    
    Returns:
        str or None: Path to the most recently read book, or None if no books found
    """
    progress_files = glob.glob(os.path.join(config.PROGRESS_FILE_DIR, "*.progress.json"))
    
    if not progress_files:
        return None
    
    most_recent_file = max(progress_files, key=os.path.getmtime)
    
    try:
        with open(most_recent_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            original_path = data.get("original_file_path")
            
            if original_path and os.path.exists(original_path):
                return original_path
                
    except (json.JSONDecodeError, IOError):
        pass
    
    return None
