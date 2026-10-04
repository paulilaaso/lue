import sys
import json
import logging
import os
import re
import time

# Default keyboard shortcuts
DEFAULT_KEYBOARD_SHORTCUTS = {
    "navigation": {
        "next_paragraph": "l",
        "prev_paragraph": "h",
        "next_sentence": "k",
        "prev_sentence": "j",
        "scroll_page_up": "i",
        "scroll_page_down": "m",
        "scroll_up": "u",
        "scroll_down": "n",
        "move_to_top_visible": "t",
        "next_chapter": "x",
        "prev_chapter": "z",
        "move_to_beginning": "y",
        "move_to_end": "b"
    },
    "tts_controls": {
        "play_pause": ["p", " "],
        "decrease_speed": ",",
        "increase_speed": ".",
        "toggle_sentence_highlight": "s",
        "toggle_word_highlight": "w"
    },
    "display_controls": {
        "toggle_auto_scroll": "a",
        "cycle_ui_complexity": "v",
        "toggle_chapter_index": "c"
    },
    "application": {
        "quit": "q",
        "toggle_recent_menu": "r",
        "select_menu_item": "\n"
    }
}

# Global variable to store loaded keyboard shortcuts
KEYBOARD_SHORTCUTS = DEFAULT_KEYBOARD_SHORTCUTS

def _matches_shortcut(data, shortcut):
    """Check if input data matches a shortcut (string or list)."""
    if isinstance(shortcut, list):
        return data in shortcut
    return data == shortcut

def load_keyboard_shortcuts(file_path=None):
    """Load keyboard shortcuts from a JSON file or use defaults.
    
    If file_path is None, the function will attempt to load from the default locations.
    """
    global KEYBOARD_SHORTCUTS
    
    # If no file path provided, use the default file
    if not file_path:
        file_path = os.path.join(os.path.dirname(__file__), 'keys_default.json')
    
    try:
        with open(file_path, 'r') as f:
            KEYBOARD_SHORTCUTS = json.load(f)
    except Exception:
        # Fallback to default shortcuts if file cannot be loaded
        KEYBOARD_SHORTCUTS = DEFAULT_KEYBOARD_SHORTCUTS

def _process_escape_sequence(reader, seq):
    """Process a parsed CSI or escape sequence (such as arrow keys)."""
    if not seq:
        return

    last_char = seq[-1]
    cmd = None

    if last_char == 'A':
        cmd = 'scroll_up' if (reader.show_recent_menu or reader.show_chapter_index) else 'prev_paragraph'
    elif last_char == 'B':
        cmd = 'scroll_down' if (reader.show_recent_menu or reader.show_chapter_index) else 'next_paragraph'
    elif last_char == 'C' and not (reader.show_recent_menu or reader.show_chapter_index):
        cmd = 'next_sentence'
    elif last_char == 'D' and not (reader.show_recent_menu or reader.show_chapter_index):
        cmd = 'prev_sentence'
    elif seq == '\x1b[5~':
        cmd = 'scroll_page_up'
    elif seq == '\x1b[6~':
        cmd = 'scroll_page_down'
    elif seq in ('\x1b[1~', '\x1b[H'):
        cmd = 'move_to_beginning'
    elif seq in ('\x1b[4~', '\x1b[F'):
        cmd = 'move_to_end'

    if cmd:
        if cmd in ('prev_paragraph', 'next_paragraph', 'prev_sentence', 'next_sentence'):
            _kill_audio_immediately(reader)
        reader.post_command(cmd)


def _process_mouse_sequence(reader, sequence):
    """Process a mouse escape sequence."""
    if not sequence or len(sequence) <= 3:
        return

    mouse_part = sequence[3:]
    if mouse_part.endswith('M') or mouse_part.endswith('m'):
        try:
            parts = mouse_part[:-1].split(';')
            if len(parts) >= 3:
                button = int(parts[0])
                x_pos = int(parts[1])
                y_pos = int(parts[2])

                if mouse_part.endswith('M'):
                    if button == 0:
                        if reader.show_chapter_index:
                            reader.post_command(('chapter_click', (x_pos, y_pos)))
                            return

                        if reader._is_click_on_subtitle(x_pos, y_pos):
                            if reader._handle_subtitle_click(x_pos, y_pos):
                                return

                        if reader._is_click_on_progress_bar(x_pos, y_pos):
                            if reader._handle_progress_bar_click(x_pos, y_pos):
                                return

                        if not reader._is_click_on_text(x_pos, y_pos):
                            return

                        if hasattr(reader, 'pending_restart_task') and reader.pending_restart_task and not reader.pending_restart_task.done():
                            reader.pending_restart_task.cancel()

                        _kill_audio_immediately(reader)
                        reader.post_command(('click_jump', (x_pos, y_pos)))
                    elif button == 64:
                        if reader.auto_scroll_enabled:
                            reader.auto_scroll_enabled = False
                        reader.post_command('wheel_scroll_up')
                    elif button == 65:
                        if reader.auto_scroll_enabled:
                            reader.auto_scroll_enabled = False
                        reader.post_command('wheel_scroll_down')
        except (ValueError, IndexError):
            pass


def _process_normal_key(reader, data):
    """Process a standard non-escape key press."""
    nav_shortcuts = KEYBOARD_SHORTCUTS.get("navigation", {})
    tts_shortcuts = KEYBOARD_SHORTCUTS.get("tts_controls", {})
    display_shortcuts = KEYBOARD_SHORTCUTS.get("display_controls", {})
    app_shortcuts = KEYBOARD_SHORTCUTS.get("application", {})

    if _matches_shortcut(data, app_shortcuts.get("quit", "q")):
        reader.running = False
        reader.command_received_event.set()
        return

    cmd = None
    if _matches_shortcut(data, app_shortcuts.get("toggle_recent_menu", "r")):
        cmd = 'toggle_recent_menu'
    elif _matches_shortcut(data, app_shortcuts.get("select_menu_item", "\n")) or data == '\r':
        cmd = 'select_menu_item'
    elif _matches_shortcut(data, tts_shortcuts.get("play_pause", "p")):
        cmd = 'pause'
    elif _matches_shortcut(data, nav_shortcuts.get("prev_paragraph", "h")):
        cmd = 'prev_paragraph'
    elif _matches_shortcut(data, nav_shortcuts.get("prev_sentence", "j")):
        cmd = 'prev_sentence'
    elif _matches_shortcut(data, nav_shortcuts.get("next_sentence", "k")):
        cmd = 'next_sentence'
    elif _matches_shortcut(data, nav_shortcuts.get("next_paragraph", "l")):
        cmd = 'next_paragraph'
    elif _matches_shortcut(data, nav_shortcuts.get("next_chapter", "x")):
        cmd = 'next_chapter'
    elif _matches_shortcut(data, nav_shortcuts.get("prev_chapter", "z")):
        cmd = 'prev_chapter'
    elif _matches_shortcut(data, nav_shortcuts.get("scroll_page_up", "i")):
        cmd = 'scroll_page_up'
    elif _matches_shortcut(data, nav_shortcuts.get("scroll_page_down", "m")):
        cmd = 'scroll_page_down'
    elif _matches_shortcut(data, nav_shortcuts.get("scroll_up", "u")):
        cmd = 'scroll_up'
    elif _matches_shortcut(data, nav_shortcuts.get("scroll_down", "n")):
        cmd = 'scroll_down'
    elif _matches_shortcut(data, display_shortcuts.get("toggle_auto_scroll", "a")):
        cmd = 'toggle_auto_scroll'
    elif _matches_shortcut(data, nav_shortcuts.get("move_to_top_visible", "t")):
        cmd = 'move_to_top_visible'
    elif _matches_shortcut(data, nav_shortcuts.get("move_to_beginning", "y")):
        cmd = 'move_to_beginning'
    elif _matches_shortcut(data, nav_shortcuts.get("move_to_end", "b")):
        cmd = 'move_to_end'
    elif _matches_shortcut(data, tts_shortcuts.get("decrease_speed", ",")):
        cmd = 'decrease_speed'
    elif _matches_shortcut(data, tts_shortcuts.get("increase_speed", ".")):
        cmd = 'increase_speed'
    elif _matches_shortcut(data, tts_shortcuts.get("toggle_sentence_highlight", "s")):
        cmd = 'toggle_sentence_highlight'
    elif _matches_shortcut(data, tts_shortcuts.get("toggle_word_highlight", "w")):
        cmd = 'toggle_word_highlight'
    elif _matches_shortcut(data, display_shortcuts.get("cycle_ui_complexity", "v")):
        cmd = 'cycle_ui_complexity'
    elif _matches_shortcut(data, display_shortcuts.get("toggle_chapter_index", "c")):
        cmd = 'toggle_chapter_index'

    if cmd:
        reader.post_command(cmd)


def process_input(reader):
    """Process user input from stdin."""
    try:
        data_bytes = os.read(sys.stdin.fileno(), 1024)
        if not data_bytes:
            return

        chars = data_bytes.decode('utf-8', errors='ignore')
        now = time.time()

        if getattr(reader, 'esc_start_time', None) is not None:
            if now - reader.esc_start_time > 0.05:
                reader.mouse_sequence_buffer = ""
                reader.esc_start_time = None

        if not hasattr(reader, 'mouse_sequence_buffer'):
            reader.mouse_sequence_buffer = ""

        if not reader.mouse_sequence_buffer and chars.startswith('\x1b'):
            reader.esc_start_time = now
        elif reader.mouse_sequence_buffer.startswith('\x1b') and getattr(reader, 'esc_start_time', None) is None:
            reader.esc_start_time = now

        reader.mouse_sequence_buffer += chars

        while reader.mouse_sequence_buffer:
            buf = reader.mouse_sequence_buffer

            if not buf.startswith('\x1b'):
                reader.mouse_sequence_buffer = buf[1:]
                _process_normal_key(reader, buf[0])
                continue

            if len(buf) == 1:
                break

            second_char = buf[1]
            if second_char not in ('[', 'O'):
                reader.mouse_sequence_buffer = buf[1:]
                reader.esc_start_time = None
                continue

            if buf.startswith('\x1b[<'):
                match = re.search(r'[Mm]', buf)
                if match:
                    end_idx = match.end()
                    seq = buf[:end_idx]
                    reader.mouse_sequence_buffer = buf[end_idx:]
                    reader.esc_start_time = None
                    _process_mouse_sequence(reader, seq)
                    continue
                if len(buf) > 64:
                    reader.mouse_sequence_buffer = ""
                    reader.esc_start_time = None
                break

            if second_char == 'O':
                if len(buf) >= 3:
                    seq = buf[:3]
                    reader.mouse_sequence_buffer = buf[3:]
                    reader.esc_start_time = None
                    _process_escape_sequence(reader, seq)
                    continue
                break

            match = re.search(r'[\x40-\x7E]', buf[2:])
            if match:
                end_idx = match.start() + 3
                seq = buf[:end_idx]
                reader.mouse_sequence_buffer = buf[end_idx:]
                reader.esc_start_time = None
                _process_escape_sequence(reader, seq)
                continue

            if len(buf) > 32:
                reader.mouse_sequence_buffer = ""
                reader.esc_start_time = None
            break
    except Exception:
        # Ne pas planter la boucle d'entrée, mais ne plus masquer les bugs.
        logging.debug("Erreur dans process_input", exc_info=True)


def _kill_audio_immediately(reader):
    """Kill audio playback immediately.

    Seuls les processus ffplay lancés par Lue (reader.playback_processes) sont
    tués. L'ancien `pkill -f ffplay` visait TOUS les ffplay de la machine et
    bloquait la boucle événementielle le temps de l'appel.
    """
    for process in reader.playback_processes[:]:
        try:
            process.kill()
        except (ProcessLookupError, AttributeError):
            pass
