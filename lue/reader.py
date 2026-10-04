import os
import sys
import asyncio
import re
import signal
import logging
import subprocess
from rich.console import Console
from rich.text import Text

from . import config, content_parser, progress_manager, audio, ui, input_handler
from .tts.base import TTSBase, prepare_text_chunk, init_prosody
from .timing_calculator import split_highlight_units

class Lue:
    def __init__(self, file_path, tts_model: TTSBase | None, overlap: float | None = None):
        self.console = Console()
        self.loop = None
        self.file_path = file_path
        self.book_title = os.path.splitext(os.path.basename(file_path))[0]
        self.progress_file = progress_manager.get_progress_file_path(self.book_title)
        self.overlap_override = overlap
        
        self._initialize_state()
        self._initialize_tts(tts_model)
        self._initialize_prosody_pipeline()
        self._load_content()
        self._initialize_progress()
        self._initialize_ui_state()
        
    def _initialize_state(self):
        """Initialize basic application state."""
        self.running = True
        self.command = None
        self.pending_commands = []  # Sequential command list to avoid race conditions
        self.playback_processes = []
        self.producer_task = None
        self.player_task = None
        self.ui_update_task = None
        self.command_received_event = asyncio.Event()
        self.playback_finished_event = asyncio.Event()
        self.audio_queue = asyncio.Queue(maxsize=config.MAX_QUEUE_SIZE)
        self.active_playback_tasks = []
        self.audio_restart_lock = asyncio.Lock()
        self.pending_restart_task = None
        self.playback_speed = 1.0  # Default speed multiplier
        
        # Split View selection state
        self.split_chapter_selection_idx = 0
        self.split_view_enabled = config.SPLIT_VIEW_ENABLED
        self._cached_chapter_titles = None

        # Lazily cached sentence lists, keyed by (chapter_idx, paragraph_idx).
        # The cache is scoped to the current book and never changes sentence contents.
        self._sentence_cache = {}
        
        # Add pause toggle lock and task tracking
        self.pause_toggle_lock = asyncio.Lock()
        self.current_pause_toggle_task = None

        # Recent books menu state
        self.show_recent_menu = False
        self.recent_books_list = []
        self.recent_menu_selection_idx = 0

        # Chapter index overlay state
        self.show_chapter_index = False
        self.chapter_index_selection_idx = 0
        self.chapter_index_scroll_offset = 0
        self.chapter_index_panel_x = 0
        self.chapter_index_panel_y = 0
        self.chapter_index_panel_width = 0
        self.chapter_index_panel_height = 0

    def _initialize_tts(self, tts_model):
        """Initialize TTS-related state."""
        self.tts_model = tts_model
        self.tts_voice = tts_model.voice if tts_model and tts_model.voice else config.TTS_VOICES.get(tts_model.name) if tts_model else None
        
    def _initialize_prosody_pipeline(self):
        """Préchauffe la préparation du texte TTS et affiche le niveau actif
        (basique / prosodie / luxe selon les modules optionnels présents)."""
        lang = getattr(self.tts_model, "lang", None)
        voice = getattr(self.tts_model, "voice", None)
        try:
            self.console.print(f"[green]{init_prosody(lang, voice)}[/green]")
        except Exception as exc:
            logging.warning("Initialisation prosodie échouée : %s", exc, exc_info=True)

    def prepare_tts_text(self, text):
        """Texte prêt pour le TTS (même chemin que tts/base.py, un seul point d'entrée)."""
        sanitized = content_parser.sanitize_text_for_tts(text)
        if not sanitized:
            return ""
        return prepare_text_chunk(
            sanitized,
            getattr(self.tts_model, "lang", None),
            getattr(self.tts_model, "voice", None),
        )

    def _load_content(self, quiet=False):
        """Load and process the document content."""
        if not quiet:
            self.console.print(f"[bold cyan]Loading document: {self.book_title}...[/bold cyan]")
        self.chapters = content_parser.extract_content(self.file_path, self.console)
        
        if not self.chapters or not any(chapter for chapter in self.chapters):
            self.console.print(f"[bold red]Error: No text could be extracted from the file.[/bold red]")
            self.console.print("This might happen with image-based PDFs or unsupported formats.")
            sys.exit(1)
            
        if not quiet:
            self.console.print(f"[green]Document loaded successfully![/green]")
            self.console.print(f"[bold cyan]Loading TTS model...[/bold cyan]")
        
        # Invalider le cache des phrases lors du chargement du contenu
        self._sentence_cache.clear()

        self.document_lines = []
        self.line_to_position = {}
        self.position_to_line = {}
        self.paragraph_line_ranges = {}
        
        self.total_sentences = sum(
            len(self.get_sentences(chap_idx, para_idx))
            for chap_idx, chapter in enumerate(self.chapters)
            for para_idx, _ in enumerate(chapter)
        )
        
        ui.update_document_layout(self)

    def get_sentences(self, chapter_idx, paragraph_idx):
        """Return the cached sentence list for a paragraph (OPTIMISATION P2)."""
        key = (chapter_idx, paragraph_idx)
        sentences = self._sentence_cache.get(key)
        if sentences is None:
            paragraph = self.chapters[chapter_idx][paragraph_idx]
            sentences = content_parser.split_into_sentences(paragraph)
            self._sentence_cache[key] = sentences
        return sentences

    async def _switch_book(self, new_path):
        """Switch to a different book."""
        self._save_extended_progress()
        await audio.stop_and_clear_audio(self)
        
        self.file_path = new_path
        self.book_title = os.path.splitext(os.path.basename(new_path))[0]
        self.progress_file = progress_manager.get_progress_file_path(self.book_title)
        
        self._load_content(quiet=True)
        self._sentence_cache.clear()
        self._initialize_progress()
        
        self.first_sentence_jump = False
        self.recent_books_list = []
        self.show_recent_menu = False
        
        asyncio.create_task(ui.display_ui(self))

    def _chapter_name(self, idx):
        """Return a human-readable name for a chapter by index."""
        chapter = self.chapters[idx]
        for para in chapter:
            stripped = para.strip()
            if stripped and len(stripped) > 3:
                return stripped[:60]
        return f"Chapter {idx + 1}"

    def _update_chapter_index_scroll(self):
        """Keep the chapter index scroll window aligned with the selection."""
        total = len(self.chapters)
        _, height = ui.get_terminal_size()
        panel_height = min(total + 4, height - 4)
        max_visible = max(1, panel_height - 4)
        offset = self.chapter_index_scroll_offset
        if offset > total - max_visible:
            offset = max(0, total - max_visible)
        if offset < 0:
            offset = 0
        sel = self.chapter_index_selection_idx
        if sel < offset:
            offset = sel
        if sel >= offset + max_visible:
            offset = max(0, sel - max_visible + 1)
        self.chapter_index_scroll_offset = offset

    def _get_first_valid_paragraph_in_chapter(self, chapter_idx):
        """Return the first non-formatting paragraph index in a chapter."""
        for i, para in enumerate(self.chapters[chapter_idx]):
            if para.strip() and len(para.strip()) > 3:
                return i
        return 0

    async def _jump_to_chapter(self, chapter_idx):
        """Jump to a specific chapter by index."""
        if not 0 <= chapter_idx < len(self.chapters):
            return
        self.show_chapter_index = False
        para_idx = self._get_first_valid_paragraph_in_chapter(chapter_idx)
        self.chapter_idx = self.ui_chapter_idx = chapter_idx
        self.paragraph_idx = self.ui_paragraph_idx = para_idx
        self.sentence_idx = self.ui_sentence_idx = 0
        self.first_sentence_jump = False
        self._scroll_to_position_immediate(chapter_idx, para_idx, 0)
        self._save_extended_progress(sync_audio_position=True)
        if not self.is_paused and self.tts_model:
            self.pending_restart_task = asyncio.create_task(self._restart_audio_after_navigation())
        else:
            await audio.stop_and_clear_audio(self)
        asyncio.create_task(ui.display_ui(self))

    def _initialize_progress(self):
        """Initialize reading progress from saved state."""
        progress_data = progress_manager.load_extended_progress(self.progress_file)
        c, p, s = progress_data["c"], progress_data["p"], progress_data["s"]
        
        self.chapter_idx, self.paragraph_idx, self.sentence_idx = (
            progress_manager.validate_and_set_progress(self.chapters, self.progress_file, c, p, s)
        )
        self.ui_chapter_idx = self.chapter_idx
        self.ui_paragraph_idx = self.paragraph_idx
        self.ui_sentence_idx = self.sentence_idx
        self.ui_word_idx = 0
        
        self.scroll_offset = progress_data["scroll_offset"]
        self.auto_scroll_enabled = progress_data["auto_scroll_enabled"]
        if getattr(config, "UI_MODE_OVERRIDE", False):
            self.speed_reading_enabled = (config.UI_MODE == 3)
            self.split_view_enabled = (config.UI_MODE == 4)
        else:
            self.speed_reading_enabled = progress_data.get("speed_reading_enabled", False)
        if self.speed_reading_enabled:
            config.UI_MODE = 3
        self.is_paused = not progress_data["tts_enabled"]
        self.playback_speed = progress_data["playback_speed"]
        if not self.tts_model:
            self.is_paused = True
            
        manual_anchor = progress_data.get("manual_scroll_anchor")
        if manual_anchor:
            anchor_pos = tuple(manual_anchor)
            if anchor_pos in self.position_to_line:
                target_line = self.position_to_line[anchor_pos]
                self.scroll_offset = float(target_line)
                
    def _initialize_ui_state(self):
        """Initialize UI and interaction state."""
        self.ui_update_interval = 0.033
        self.target_scroll_offset = self.scroll_offset
        self.scroll_animation_speed = 0.8
        
        self.last_auto_scroll_position = (0, 0, 0)
        self.smooth_scroll_task = None
        self.last_scroll_time = 0
        self.scroll_momentum = 0
        
        self.last_mouse_event_time = 0
        self.mouse_sequence_buffer = ''
        self.mouse_sequence_active = False
        self.resize_anchor = None
        
        self.selection_active = False
        self.selection_start = None
        self.selection_end = None
        self.selection_start_pos = None
        self.selection_end_pos = None
        self.mouse_pressed = False
        self.mouse_press_pos = None
        
        self.last_rendered_state = None
        self.last_terminal_size = None
        self.render_lock = asyncio.Lock()
        self._layout_needs_update = self.speed_reading_enabled
        self.resize_scheduled = False
        self.first_sentence_jump = False
        self._initial_load_complete = True
        self.subtitle_hitboxes = []

    async def initialize_tts(self) -> bool:
        """Initializes the selected TTS model."""
        if not self.tts_model:
            self.console.print("[yellow]No TTS model selected. TTS playback is disabled.[/yellow]")
            return True
        
        initialized = await self.tts_model.initialize()
        if initialized:
            await self.tts_model.warm_up()
            return True
        else:
            self.console.print(f"[bold red]Initialization of {self.tts_model.name.upper()} failed. TTS will be disabled.[/bold red]")
            self.tts_model = None
            self.is_paused = True
            return False

    def post_command(self, cmd):
        """Post a command to the reader synchronously and thread-safely."""
        self.pending_commands.append(cmd)
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.command_received_event.set)

    def _post_command_sync(self, cmd):
        self.post_command(cmd)

    async def _post_command(self, cmd):
        self.post_command(cmd)

    def _is_position_visible(self, chapter_idx, paragraph_idx, sentence_idx):
        position_key = (chapter_idx, paragraph_idx, sentence_idx)
        if position_key not in self.position_to_line: return False
        target_line = self.position_to_line[position_key]
        _, height = ui.get_terminal_size()
        available_height = max(1, height - 4)
        return self.scroll_offset <= target_line < self.scroll_offset + available_height
    
    def _is_position_near_current_reading(self, click_pos, threshold=3):
        try:
            current_chapter, current_paragraph, current_sentence = self.chapter_idx, self.paragraph_idx, self.sentence_idx
            click_chapter, click_paragraph, click_sentence = click_pos
            
            if click_chapter != current_chapter:
                return False
                
            if click_paragraph == current_paragraph:
                return abs(click_sentence - current_sentence) <= threshold
                
            if abs(click_paragraph - current_paragraph) == 1:
                if click_paragraph < current_paragraph:
                    sentences_in_paragraph = len(self.get_sentences(click_chapter, click_paragraph))
                    return click_sentence >= max(0, sentences_in_paragraph - threshold)
                else:
                    return click_sentence <= threshold
                    
            return False
        except (IndexError, AttributeError, TypeError):
            return False
    
    def _is_paragraph_near_current_reading(self, direction):
        try:
            current_chapter, current_paragraph, current_sentence = self.chapter_idx, self.paragraph_idx, self.sentence_idx
            
            if direction == 'next':
                if current_sentence > 0:
                    return False
                return True
            else:
                sentences_in_paragraph = len(self.get_sentences(current_chapter, current_paragraph))
                if current_sentence < sentences_in_paragraph - 1:
                    return False
                return True
        except (IndexError, AttributeError, TypeError):
            return False

    def _scroll_to_position(self, chapter_idx, paragraph_idx, sentence_idx, smooth=True):
        if not smooth and self._is_position_visible(chapter_idx, paragraph_idx, sentence_idx): return
        position_key = (chapter_idx, paragraph_idx, sentence_idx)
        if position_key in self.position_to_line:
            target_line = self.position_to_line[position_key]
            _, height = ui.get_terminal_size()
            
            if getattr(self, 'split_view_enabled', False) or config.UI_MODE == 4:
                panel_height = max(1, height - 2)
                available_height = max(1, panel_height - 4)
                vertical_center_offset = 2  
            else:
                available_height = max(1, height - 4)
                vertical_center_offset = 0

            if hasattr(self, 'first_sentence_jump') and self.first_sentence_jump:
                new_offset = max(0, target_line) if self._is_position_visible(chapter_idx, paragraph_idx, sentence_idx) else max(0, target_line)
            else:
                new_offset = max(0, target_line - (available_height // 2) + vertical_center_offset)
                
            max_scroll = max(0, len(self.document_lines) - available_height)
            new_offset = min(new_offset, max_scroll)
            if smooth: self._smooth_scroll_to(new_offset)
            else: self.scroll_offset = self.target_scroll_offset = new_offset

    def _smooth_scroll_to(self, target_offset, fast=False):
        self.target_scroll_offset = max(0, min(target_offset, len(self.document_lines) - 1))
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self.smooth_scroll_task = asyncio.create_task(self._animate_scroll(fast))

    async def _animate_scroll(self, fast=False):
        try:
            if fast:
                self.scroll_offset = self.target_scroll_offset
                return
            start_offset, target_offset = self.scroll_offset, self.target_scroll_offset
            if abs(target_offset - start_offset) < 0.1: return
            animation_speed, frame_delay, convergence_threshold, min_step, steps, max_steps = 0.15, 0.03, 0.5, 2.0, 0, 80
            while abs(self.scroll_offset - self.target_scroll_offset) > convergence_threshold and steps < max_steps:
                diff = self.target_scroll_offset - self.scroll_offset
                step = diff * animation_speed
                distance = abs(diff)
                if distance > 30: step *= 2.5
                elif distance > 15: step *= 1.8
                elif distance > 5: step *= 1.2
                elif distance < 2: step *= 0.7
                if abs(step) < min_step: step = min_step if diff > 0 else -min_step
                if abs(step) > abs(diff): step = diff
                self.scroll_offset += step
                max_scroll = max(0, len(self.document_lines) - (ui.get_terminal_size()[1] - 4))
                self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))
                await asyncio.sleep(frame_delay)
                steps += 1
            self.scroll_offset = self.target_scroll_offset
        except asyncio.CancelledError: pass

    def _find_sentence_at_click(self, click_x, click_y):
        width, height = ui.get_terminal_size()
        available_height = max(1, height - 4)
        content_y, content_x = click_y - 3, click_x - 5
        if not (0 <= content_y < available_height): return None
        clicked_line = int(self.scroll_offset) + content_y
        if clicked_line >= len(self.document_lines): return None
        if clicked_line in self.line_to_position:
            chap_idx, para_idx, _ = self.line_to_position[clicked_line]
            if (chap_idx, para_idx) in self.paragraph_line_ranges:
                para_start, _ = self.paragraph_line_ranges[(chap_idx, para_idx)]
                paragraph = self.chapters[chap_idx][para_idx]
                sentences = self.get_sentences(chap_idx, para_idx)
                sentence_positions = []
                current_char = 0
                for sent_idx, sentence in enumerate(sentences):
                    sentence_positions.append((current_char, current_char + len(sentence), sent_idx))
                    current_char += len(sentence) + 1
                wrapped_lines = Text(paragraph, justify="left", no_wrap=False).wrap(self.console, max(20, width - 10))
                line_offset = clicked_line - para_start
                if 0 <= line_offset < len(wrapped_lines):
                    char_pos_in_para = sum(len(line.plain) for line in wrapped_lines[:line_offset]) + min(content_x, len(wrapped_lines[line_offset].plain))
                    for start_char, end_char, sent_idx in sentence_positions:
                        if start_char <= char_pos_in_para <= end_char:
                            return (chap_idx, para_idx, sent_idx)
        return None

    def _find_char_position_at_click(self, click_x, click_y):
        width, height = ui.get_terminal_size()
        available_height = max(1, height - 4)
        content_y, content_x = click_y - 3, click_x - 5
        if not (0 <= content_y < available_height): return None
        clicked_line = int(self.scroll_offset) + content_y
        if clicked_line >= len(self.document_lines): return None
        if clicked_line < len(self.document_lines):
            line_text = self.document_lines[clicked_line].plain
            content_x = min(content_x, len(line_text))
        return (clicked_line, content_x)

    def _is_click_on_text(self, click_x, click_y):
        width, height = ui.get_terminal_size()
        return (5 <= click_x <= width - 5 and 3 <= click_y <= height - 2)

    def _is_click_on_progress_bar(self, click_x, click_y):
        if click_y != 1: return False
        width, height = ui.get_terminal_size()
        progress_percent = self._calculate_ui_progress_percentage()
        progress_bar_width = 10
        filled_blocks = int((progress_percent / 100) * progress_bar_width)
        progress_bar = "▓" * filled_blocks + "░" * (progress_bar_width - filled_blocks)
        percentage_text = f"{int(progress_percent)}% {progress_bar}"
        available_width = width - len(percentage_text) - 6
        title_text = f"{self.book_title[:available_width-3]}..." if len(self.book_title) > available_width else self.book_title
        remaining_space = width - (len(title_text) + len(percentage_text) + 2) - 6
        connecting_line = "─" * max(0, remaining_space)
        progress_bar_start_x = 3 + len(title_text) + 1 + len(connecting_line) + 1 + len(f"{int(progress_percent)}% ")
        return progress_bar_start_x <= click_x < progress_bar_start_x + progress_bar_width

    def _handle_progress_bar_click(self, click_x, click_y):
        if not self._is_click_on_progress_bar(click_x, click_y): return False
        width, height = ui.get_terminal_size()
        progress_percent = self._calculate_ui_progress_percentage()
        progress_bar_width = 10
        filled_blocks = int((progress_percent / 100) * progress_bar_width)
        progress_bar = "▓" * filled_blocks + "░" * (progress_bar_width - filled_blocks)
        percentage_text = f"{int(progress_percent)}% {progress_bar}"
        available_width = width - len(percentage_text) - 6
        title_text = f"{self.book_title[:available_width-3]}..." if len(self.book_title) > available_width else self.book_title
        remaining_space = width - (len(title_text) + len(percentage_text) + 2) - 6
        connecting_line = "─" * max(0, remaining_space)
        progress_bar_start_x = 3 + len(title_text) + 1 + len(connecting_line) + 1 + len(f"{int(progress_percent)}% ")
        
        click_percentage = max(0, min(100, ((click_x - progress_bar_start_x) / (progress_bar_width - 1)) * 100))
        max_scroll = max(0, len(self.document_lines) - max(1, height - 4))
        self.auto_scroll_enabled = False
        self.scroll_offset = self.target_scroll_offset = max(0, min((click_percentage / 100) * max_scroll, max_scroll))
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()
        return True

    def _is_click_on_subtitle(self, click_x, click_y):
        if config.UI_MODE != 2: return False
        _, height = ui.get_terminal_size()
        return click_y == height

    def _handle_subtitle_click(self, click_x, click_y):
        for key, start_x, end_x in getattr(self, 'subtitle_hitboxes', []):
            if start_x <= click_x <= end_x:
                self.post_command(key)
                return True
        return False

    def _is_click_in_selection(self, click_pos):
        if not self.selection_active or not self.selection_start or not self.selection_end or not click_pos: return False
        start_line, start_char = self.selection_start
        end_line, end_char = self.selection_end
        if start_line > end_line or (start_line == end_line and start_char > end_char):
            start_line, start_char, end_line, end_char = end_line, end_char, start_line, start_char
        click_line, click_char = click_pos
        if start_line <= click_line <= end_line:
            if start_line == end_line: return start_char <= click_char <= end_char
            elif click_line == start_line: return click_char >= start_char
            elif click_line == end_line: return click_char <= end_char
            else: return True
        return False

    def _clear_selection(self):
        self.selection_active = False
        self.selection_start = self.selection_end = self.selection_start_pos = self.selection_end_pos = None

    def _get_selected_text(self):
        if not self.selection_active or not self.selection_start or not self.selection_end: return ""
        start_line, start_char = self.selection_start
        end_line, end_char = self.selection_end
        if start_line > end_line or (start_line == end_line and start_char > end_char):
            start_line, start_char, end_line, end_char = end_line, end_char, start_line, start_char
        selected_text = []
        for line_idx in range(start_line, end_line + 1):
            if line_idx >= len(self.document_lines): break
            line_text = self.document_lines[line_idx].plain
            if start_line == end_line: selected_text.append(line_text[max(0, min(start_char, len(line_text))):max(0, min(end_char, len(line_text)))])
            elif line_idx == start_line: selected_text.append(line_text[max(0, min(start_char, len(line_text))):])
            elif line_idx == end_line: selected_text.append(line_text[:max(0, min(end_char, len(line_text)))])
            else: selected_text.append(line_text)
        return re.sub(r' {2,}', ' ', " ".join(selected_text)).replace('\n', ' ').strip()

    def _copy_to_clipboard(self, text):
        if not text: return False
        try:
            process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE, text=True)
            process.communicate(input=text)
            return process.returncode == 0
        except (subprocess.CalledProcessError, FileNotFoundError): return False

    def _handle_copy_selection(self):
        if not self.selection_active: return False
        selected_text = self._get_selected_text()
        if selected_text and self._copy_to_clipboard(selected_text):
            self._clear_selection()
            return True
        return False

    def _increase_speed(self):
        speed_levels = [round(i * 0.1, 1) for i in range(10, 31)]
        current_index = next((i for i, speed in enumerate(speed_levels) if abs(speed - self.playback_speed) < 0.01), 0)
        if current_index < len(speed_levels) - 1:
            self.playback_speed = speed_levels[current_index + 1]
            self._save_extended_progress()
            return True
        return False

    def _decrease_speed(self):
        speed_levels = [round(i * 0.1, 1) for i in range(10, 31)]
        current_index = next((i for i, speed in enumerate(speed_levels) if abs(speed - self.playback_speed) < 0.01), 0)
        if current_index > 0:
            self.playback_speed = speed_levels[current_index - 1]
            self._save_extended_progress()
            return True
        return False

    def _get_speed_display(self):
        if abs(self.playback_speed - 1.0) < 0.01: return ""
        superscript_map = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '.': ''}
        return f"ˣ{''.join(superscript_map.get(char, char) for char in f'{self.playback_speed:.2f}')}"

    def _advance_position(self, current_pos, mode='sentence', wrap=True):
        c, p, s = current_pos
        if mode == 'paragraph': p, s = p + 1, 0
        elif mode == 'chapter':
            c += 1
            if c < len(self.chapters): p, s = self._get_first_valid_paragraph_in_chapter(c), 0
            else: p, s = 0, 0
        else: s += 1
        while c < len(self.chapters):
            if p < len(self.chapters[c]):
                if s < len(self.get_sentences(c, p)):
                    if mode == 'paragraph': s = 0
                    return c, p, s
                p, s = p + 1, 0
            else: c, p, s = c + 1, 0, 0
        return (0, 0, 0) if wrap else None

    def _rewind_position(self, current_pos, mode='sentence'):
        c, p, s = current_pos
        if mode == 'paragraph': p, s = p - 1, 0
        elif mode == 'chapter':
            c -= 1
            if c >= 0: p, s = self._get_first_valid_paragraph_in_chapter(c), 0
            else: p, s = 0, 0
        else: s -= 1

        while c >= 0:
            if p >= 0:
                if s >= 0:
                    if mode == 'paragraph': s = 0
                    return c, p, s
                p -= 1
                if p >= 0: s = len(self.get_sentences(c, p)) - 1
                else:
                    c -= 1
                    if c >= 0: p, s = len(self.chapters[c]) - 1, len(self.get_sentences(c, len(self.chapters[c]) - 1)) - 1
            else:
                c -= 1
                if c >= 0: p, s = len(self.chapters[c]) - 1, len(self.get_sentences(c, len(self.chapters[c]) - 1)) - 1
        c, p = len(self.chapters) - 1, len(self.chapters[-1]) - 1
        return c, p, len(self.get_sentences(c, p)) - 1

    def _get_topmost_visible_sentence(self):
        top_visible_line = int(self.scroll_offset)
        bottom_visible_line = top_visible_line + max(1, ui.get_terminal_size()[1] - 4)
        topmost_sentence_pos, earliest_line = None, float('inf')
        for pos, line_num in self.position_to_line.items():
            if top_visible_line <= line_num < bottom_visible_line and line_num < earliest_line:
                earliest_line, topmost_sentence_pos = line_num, pos
        if topmost_sentence_pos: return topmost_sentence_pos
        return next((pos for pos, line_num in sorted(self.position_to_line.items(), key=lambda x: x[1], reverse=True) if line_num < top_visible_line), None)
    
    def _calculate_progress_percentage(self):
        if self.total_sentences == 0: return 100.0
        sentences_read = sum(len(self.get_sentences(i, p)) for i in range(self.chapter_idx) for p in range(len(self.chapters[i])))
        if self.chapter_idx < len(self.chapters):
            sentences_read += sum(len(self.get_sentences(self.chapter_idx, i)) for i in range(self.paragraph_idx)) + self.sentence_idx
        return (sentences_read / self.total_sentences) * 100

    def _calculate_ui_progress_percentage(self):
        if len(self.document_lines) == 0: return 100.0
        max_scroll = max(0, len(self.document_lines) - max(1, ui.get_terminal_size()[1] - 4))
        if max_scroll == 0: return 100.0
        return min(100.0, max(0.0, (self.scroll_offset / max_scroll) * 100))

    def _save_extended_progress(self, sync_audio_position=False):
        if sync_audio_position:
            self.chapter_idx, self.paragraph_idx, self.sentence_idx = self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx
        progress_manager.save_extended_progress(
            self.progress_file, self.chapter_idx, self.paragraph_idx, self.sentence_idx, 
            self.scroll_offset, not self.is_paused, self.auto_scroll_enabled,
            manual_scroll_anchor=self._get_topmost_visible_sentence(),
            original_file_path=self.file_path, playback_speed=self.playback_speed,
            percentage=self._calculate_ui_progress_percentage(), speed_reading_enabled=self.speed_reading_enabled
        )

    def _scroll_to_position_immediate(self, chapter_idx, paragraph_idx, sentence_idx):
        if (chapter_idx, paragraph_idx, sentence_idx) in self.position_to_line:
            target_line = self.position_to_line[(chapter_idx, paragraph_idx, sentence_idx)]
            available_height = max(1, ui.get_terminal_size()[1] - 4)
            self.scroll_offset = self.target_scroll_offset = min(max(0, target_line - available_height // 2), max(0, len(self.document_lines) - available_height))

    def _handle_scroll_up_immediate(self):
        self.auto_scroll_enabled = False
        self.scroll_offset = self.target_scroll_offset = max(0, self.scroll_offset - 1)
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self.chapter_idx, self.paragraph_idx, self.sentence_idx = self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx
        self._save_extended_progress()

    def _handle_scroll_up_smooth(self):
        self.auto_scroll_enabled = False
        target_offset = max(0, self.scroll_offset - 1)
        if config.SMOOTH_SCROLLING_ENABLED: self._smooth_scroll_to(target_offset)
        else:
            self.scroll_offset = self.target_scroll_offset = target_offset
            if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self.chapter_idx, self.paragraph_idx, self.sentence_idx = self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx
        self._save_extended_progress()

    def _handle_scroll_down_immediate(self):
        self.auto_scroll_enabled = False
        max_scroll = max(0, len(self.document_lines) - (ui.get_terminal_size()[1] - 4))
        self.scroll_offset = self.target_scroll_offset = min(max_scroll, self.scroll_offset + 1)
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self.chapter_idx, self.paragraph_idx, self.sentence_idx = self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx
        self._save_extended_progress()

    def _handle_scroll_down_smooth(self):
        self.auto_scroll_enabled = False
        max_scroll = max(0, len(self.document_lines) - (ui.get_terminal_size()[1] - 4))
        target_offset = min(max_scroll, self.scroll_offset + 1)
        if config.SMOOTH_SCROLLING_ENABLED: self._smooth_scroll_to(target_offset)
        else:
            self.scroll_offset = self.target_scroll_offset = target_offset
            if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self.chapter_idx, self.paragraph_idx, self.sentence_idx = self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx
        self._save_extended_progress()

    def _handle_navigation_immediate(self, cmd):
        current_pos = (self.chapter_idx, self.paragraph_idx, self.sentence_idx)
        direction, mode = cmd.split('_')
        new_pos = self._advance_position(current_pos, mode) if direction == 'next' else self._rewind_position(current_pos, mode)
        if new_pos:
            self.first_sentence_jump = False
            self.chapter_idx, self.paragraph_idx, self.sentence_idx = new_pos
            self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx = new_pos
            self._scroll_to_position_immediate(*new_pos)
            self._save_extended_progress(sync_audio_position=True)

    def _handle_navigation_smooth(self, cmd):
        current_pos = (self.chapter_idx, self.paragraph_idx, self.sentence_idx)
        direction, mode = cmd.split('_')
        new_pos = self._advance_position(current_pos, mode) if direction == 'next' else self._rewind_position(current_pos, mode)
        if new_pos:
            self.first_sentence_jump = False
            self.chapter_idx, self.paragraph_idx, self.sentence_idx = new_pos
            self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx = new_pos
            if new_pos in self.position_to_line:
                target_line = self.position_to_line[new_pos]
                available_height = max(1, ui.get_terminal_size()[1] - 4)
                new_offset = min(max(0, target_line - available_height // 2), max(0, len(self.document_lines) - available_height))
                if config.SMOOTH_SCROLLING_ENABLED: self._smooth_scroll_to(new_offset)
                else: self.scroll_offset = self.target_scroll_offset = new_offset
            self._save_extended_progress(sync_audio_position=True)

    async def _restart_audio_after_navigation(self):
        async with self.audio_restart_lock:
            if self.pending_restart_task and not self.pending_restart_task.done():
                self.pending_restart_task.cancel()
                try: await self.pending_restart_task
                except asyncio.CancelledError: pass
            await audio.stop_and_clear_audio(self)
            await asyncio.sleep(0.1)
            if not self.is_paused and self.running:
                await audio.play_from_current_position(self)

    def _handle_page_scroll_immediate(self, direction):
        self.auto_scroll_enabled = False
        page_size = max(1, ui.get_terminal_size()[1] - 4)
        new_offset = max(0, self.scroll_offset - page_size) if direction < 0 else min(max(0, len(self.document_lines) - page_size), self.scroll_offset + page_size)
        self.scroll_offset = self.target_scroll_offset = new_offset
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_page_scroll_smooth(self, direction):
        self.auto_scroll_enabled = False
        page_size = max(1, ui.get_terminal_size()[1] - 4)
        target_offset = max(0, self.scroll_offset - page_size) if direction < 0 else min(max(0, len(self.document_lines) - page_size), self.scroll_offset + page_size)
        if config.SMOOTH_SCROLLING_ENABLED: self._smooth_scroll_to(target_offset)
        else:
            self.scroll_offset = self.target_scroll_offset = target_offset
            if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_move_to_top_immediate(self):
        top_visible_line, bottom_visible_line = int(self.scroll_offset), int(self.scroll_offset) + max(1, ui.get_terminal_size()[1] - 4)
        topmost_sentence, topmost_line = None, float('inf')
        for pos, line in self.position_to_line.items():
            if top_visible_line <= line < bottom_visible_line and line < topmost_line:
                topmost_line, topmost_sentence = line, pos
        if topmost_sentence:
            self.chapter_idx, self.paragraph_idx, self.sentence_idx = topmost_sentence
            self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx = topmost_sentence
            self.first_sentence_jump = True
        self.auto_scroll_enabled = True
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_move_to_top_smooth(self):
        top_visible_line, bottom_visible_line = int(self.scroll_offset), int(self.scroll_offset) + max(1, ui.get_terminal_size()[1] - 4)
        topmost_sentence, topmost_line = None, float('inf')
        for pos, line in self.position_to_line.items():
            if top_visible_line <= line < bottom_visible_line and line < topmost_line:
                topmost_line, topmost_sentence = line, pos
        if topmost_sentence:
            self.chapter_idx, self.paragraph_idx, self.sentence_idx = topmost_sentence
            self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx = topmost_sentence
            self.first_sentence_jump = True
        self.auto_scroll_enabled = True
        if config.SMOOTH_SCROLLING_ENABLED and topmost_sentence and topmost_sentence in self.position_to_line:
            target_line = self.position_to_line[topmost_sentence]
            available_height = max(1, ui.get_terminal_size()[1] - 4)
            target_offset = max(0, min(target_line - available_height // 2, max(0, len(self.document_lines) - available_height)))
            self._smooth_scroll_to(target_offset)
        else:
            if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_move_to_beginning_immediate(self):
        self.auto_scroll_enabled = False
        self.scroll_offset = self.target_scroll_offset = 0
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_move_to_beginning_smooth(self):
        self.auto_scroll_enabled = False
        if config.SMOOTH_SCROLLING_ENABLED: self._smooth_scroll_to(0)
        else:
            self.scroll_offset = self.target_scroll_offset = 0
            if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_move_to_end_immediate(self):
        self.auto_scroll_enabled = False
        max_scroll = max(0, len(self.document_lines) - max(1, ui.get_terminal_size()[1] - 4))
        self.scroll_offset = self.target_scroll_offset = max_scroll
        if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    def _handle_move_to_end_smooth(self):
        self.auto_scroll_enabled = False
        max_scroll = max(0, len(self.document_lines) - max(1, ui.get_terminal_size()[1] - 4))
        if config.SMOOTH_SCROLLING_ENABLED: self._smooth_scroll_to(max_scroll)
        else:
            self.scroll_offset = self.target_scroll_offset = max_scroll
            if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
        self._save_extended_progress()

    async def _handle_pause_toggle(self):
        async with self.pause_toggle_lock:
            if self.current_pause_toggle_task and not self.current_pause_toggle_task.done():
                self.current_pause_toggle_task.cancel()
                try: await self.current_pause_toggle_task
                except asyncio.CancelledError: pass
            await audio.stop_and_clear_audio(self)
            if not self.is_paused and self.running and self.tts_model:
                await audio.play_from_current_position(self)

    def _handle_resize(self, signum, frame):
        if not self.resize_scheduled:
            if not self.auto_scroll_enabled:
                top_sentence = self._get_topmost_visible_sentence()
                if top_sentence and top_sentence in self.position_to_line:
                    fraction = max(0.0, min(1.0, (self.position_to_line[top_sentence] - self.scroll_offset) / max(1, ui.get_terminal_size()[1] - 4)))
                    self.resize_anchor = (top_sentence, fraction)
            self.resize_scheduled = True
            self.loop.call_soon_threadsafe(self._post_command_sync, '_resize')

    async def _ui_update_loop(self):
        last_sentence_pos, last_progress_save_time = None, asyncio.get_event_loop().time()
        while self.running:
            try:
                current_time = asyncio.get_event_loop().time()
                if not self.is_paused:
                    target_pos = (self.chapter_idx, self.paragraph_idx, self.sentence_idx)
                    if target_pos != (self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx):
                        if self.first_sentence_jump and last_sentence_pos is not None and target_pos != last_sentence_pos:
                            self.first_sentence_jump = False
                        self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx = target_pos
                        if self.auto_scroll_enabled:
                            self.last_auto_scroll_position = target_pos
                            self._scroll_to_position(*target_pos)
                        last_sentence_pos = target_pos

                if (current_time - last_progress_save_time) >= 5.0:
                    self._save_extended_progress()
                    last_progress_save_time = current_time
                
                await ui.display_ui(self)
                await asyncio.sleep(self.ui_update_interval)
            except asyncio.CancelledError: break
            except Exception as e:
                logging.error(f"Error in UI update loop: {e}", exc_info=True)
                await asyncio.sleep(self.ui_update_interval)

    async def _word_update_loop(self):
        while self.running:
            try:
                if (not self.is_paused and hasattr(self, 'current_sentence_words') and
                    hasattr(self, 'current_sentence_duration') and hasattr(self, 'current_word_start_time') and
                    self.current_sentence_words and self.current_sentence_duration > 0):
                    
                    adjusted_elapsed = (asyncio.get_event_loop().time() - self.current_word_start_time) * self.playback_speed
                    total_words = len(self.current_sentence_words)
                    if total_words > 0:
                        current_word_idx = 0
                        if hasattr(self, 'current_word_timings') and self.current_word_timings:
                            if hasattr(self, 'current_word_mapping') and self.current_word_mapping:
                                tts_word_idx, tts_word_start, tts_word_end = None, None, None
                                for i, (word, start_time, end_time) in enumerate(self.current_word_timings):
                                    if adjusted_elapsed >= start_time and adjusted_elapsed < end_time:
                                        tts_word_idx, tts_word_start, tts_word_end = i, start_time, end_time
                                        break
                                if tts_word_idx is None and self.current_word_timings:
                                    if adjusted_elapsed >= max(end for _, _, end in self.current_word_timings):
                                        tts_word_idx = len(self.current_word_timings) - 1
                                        _, tts_word_start, tts_word_end = self.current_word_timings[tts_word_idx]
                                if tts_word_idx is not None:
                                    mapped_orig = [orig for orig, mapped in enumerate(self.current_word_mapping) if mapped == tts_word_idx]
                                    if mapped_orig:
                                        if len(mapped_orig) == 1: current_word_idx = mapped_orig[0]
                                        else:
                                            sub_idx = min(int((adjusted_elapsed - tts_word_start) / ((tts_word_end - tts_word_start) / len(mapped_orig))), len(mapped_orig) - 1)
                                            current_word_idx = mapped_orig[sub_idx]
                                    else:
                                        # Mot TTS sans équivalent dans le texte (nombre développé
                                        # en lettres…) : on reste sur le dernier mot déjà atteint.
                                        previous = [orig for orig, mapped in enumerate(self.current_word_mapping) if mapped < tts_word_idx]
                                        current_word_idx = min(previous[-1] if previous else 0, total_words - 1)
                            else:
                                for i, (word, start_time, end_time) in enumerate(self.current_word_timings):
                                    if adjusted_elapsed >= start_time and adjusted_elapsed < end_time:
                                        current_word_idx = min(i, total_words - 1)
                                        break
                                else:
                                    if self.current_word_timings and adjusted_elapsed >= max(end for _, _, end in self.current_word_timings):
                                        current_word_idx = total_words - 1
                        else:
                            current_word_idx = min(int(adjusted_elapsed / (self.current_sentence_duration / total_words)), total_words - 1)

                        if current_word_idx != self.ui_word_idx:
                            self.ui_word_idx = current_word_idx
                await asyncio.sleep(0.05)
            except asyncio.CancelledError: break
            except Exception as exc:
                logging.exception("Word update loop failed: %s", exc)
                await asyncio.sleep(0.05)

    async def _shutdown(self):
        self.running = False
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        
        tasks_to_cancel = [
            self.smooth_scroll_task, self.ui_update_task, self.word_update_task, 
            self.pending_restart_task, self.current_pause_toggle_task,
            self.producer_task, self.player_task
        ]
        for task in tasks_to_cancel:
            if task and not task.done(): task.cancel()
        
        valid_tasks = [t for t in tasks_to_cancel if t is not None]
        if valid_tasks: await asyncio.gather(*valid_tasks, return_exceptions=True)
        
        await audio.stop_and_clear_audio(self)
        self._save_extended_progress()
        sys.stdout.write('\033[?1049l\033[?1002l\033[?25h')
        sys.stdout.flush()

    def _handle_exit_signal(self, signum, frame):
        self._save_extended_progress()
        self.running = False
        if self.loop and self.loop.is_running(): self.loop.call_soon_threadsafe(self._post_command_sync, 'quit')

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.loop.add_reader(sys.stdin.fileno(), input_handler.process_input, self)
        sys.stdout.write('\033[?1002h')
        sys.stdout.flush()
        
        signal.signal(signal.SIGWINCH, self._handle_resize)
        signal.signal(signal.SIGINT, self._handle_exit_signal)
        signal.signal(signal.SIGTERM, self._handle_exit_signal)
        
        if not self.chapters or not self.chapters[0]: return
            
        self.ui_update_task = asyncio.create_task(self._ui_update_loop())
        self.word_update_task = asyncio.create_task(self._word_update_loop())
        
        await audio.play_from_current_position(self)
        
        while self.running:
            await self.command_received_event.wait()
            self.command_received_event.clear()

            if not self.pending_commands: continue
            cmd = self.pending_commands.pop(0)
            if self.pending_commands: self.command_received_event.set()

            if not cmd: continue

            if getattr(self, 'split_view_enabled', False) or config.UI_MODE == 4:
                if len(self.chapters) > 1:
                    if not hasattr(self, 'split_chapter_selection_idx'):
                        self.split_chapter_selection_idx = self.chapter_idx

                    if cmd == 'prev_chapter':
                        self.split_chapter_selection_idx = (self.split_chapter_selection_idx - 1) % len(self.chapters)
                        asyncio.create_task(ui.display_ui(self))
                        continue
                    elif cmd == 'next_chapter':
                        self.split_chapter_selection_idx = (self.split_chapter_selection_idx + 1) % len(self.chapters)
                        asyncio.create_task(ui.display_ui(self))
                        continue
                    elif cmd == 'select_menu_item':
                        if 0 <= self.split_chapter_selection_idx < len(self.chapters):
                            await self._jump_to_chapter(self.split_chapter_selection_idx)
                        continue

            if cmd == 'toggle_recent_menu':
                self.show_recent_menu = not self.show_recent_menu
                if self.show_recent_menu:
                    self.show_chapter_index = False
                    self.recent_books_list = progress_manager.get_recent_books()
                    self.recent_menu_selection_idx = 0
                    if not self.is_paused:
                        self.is_paused = True
                        self._save_extended_progress()
                        await audio.stop_and_clear_audio(self)
                asyncio.create_task(ui.display_ui(self))
                continue

            if cmd == 'toggle_chapter_index':
                if len(self.chapters) <= 1: continue
                self.show_chapter_index = not self.show_chapter_index
                if self.show_chapter_index:
                    self.show_recent_menu = False
                    self.chapter_index_selection_idx = self.chapter_idx
                    self._update_chapter_index_scroll()
                    if not self.is_paused:
                        self.is_paused = True
                        self._save_extended_progress()
                        await audio.stop_and_clear_audio(self)
                asyncio.create_task(ui.display_ui(self))
                continue

            if self.show_chapter_index:
                if cmd in ['prev_sentence', 'prev_paragraph', 'scroll_up', 'scroll_page_up', 'wheel_scroll_up', 'move_to_beginning', 'prev_chapter']:
                    self.chapter_index_selection_idx = (self.chapter_index_selection_idx - 1) % len(self.chapters)
                    self._update_chapter_index_scroll()
                    asyncio.create_task(ui.display_ui(self))
                    continue
                elif cmd in ['next_sentence', 'next_paragraph', 'scroll_down', 'scroll_page_down', 'wheel_scroll_down', 'move_to_end', 'next_chapter']:
                    self.chapter_index_selection_idx = (self.chapter_index_selection_idx + 1) % len(self.chapters)
                    self._update_chapter_index_scroll()
                    asyncio.create_task(ui.display_ui(self))
                    continue
                elif cmd == 'select_menu_item':
                    if 0 <= self.chapter_index_selection_idx < len(self.chapters):
                        await self._jump_to_chapter(self.chapter_index_selection_idx)
                    continue
                elif cmd == 'toggle_chapter_index':
                    self.show_chapter_index = False
                    asyncio.create_task(ui.display_ui(self))
                    continue
                elif cmd in ['quit', '_resize'] or isinstance(cmd, tuple): pass
                else: continue

            if self.show_recent_menu:
                if cmd in ['prev_sentence', 'prev_paragraph', 'prev_chapter', 'scroll_up', 'scroll_page_up', 'wheel_scroll_up', 'move_to_beginning']:
                    if self.recent_books_list:
                        self.recent_menu_selection_idx = (self.recent_menu_selection_idx - 1) % len(self.recent_books_list)
                    asyncio.create_task(ui.display_ui(self))
                    continue
                elif cmd in ['next_sentence', 'next_paragraph', 'next_chapter', 'scroll_down', 'scroll_page_down', 'wheel_scroll_down', 'move_to_end']:
                    if self.recent_books_list:
                        self.recent_menu_selection_idx = (self.recent_menu_selection_idx + 1) % len(self.recent_books_list)
                    asyncio.create_task(ui.display_ui(self))
                    continue
                elif cmd == 'select_menu_item':
                    if self.recent_books_list and 0 <= self.recent_menu_selection_idx < len(self.recent_books_list):
                        await self._switch_book(self.recent_books_list[self.recent_menu_selection_idx]['path'])
                    continue
                elif cmd in ['quit', '_resize']: pass
                else: continue
            
            if isinstance(cmd, tuple):
                command_name, data = cmd
                if command_name == '_update_highlight':
                    if not self.is_paused: self.chapter_idx, self.paragraph_idx, self.sentence_idx = data
                elif command_name == '_new_sentence_started':
                    c, p, s, duration, timing_data = data
                    self.chapter_idx, self.paragraph_idx, self.sentence_idx = c, p, s
                    self.ui_word_idx = 0
                    timing_info = timing_data if isinstance(timing_data, dict) else {"word_timings": timing_data, "speech_duration": duration, "total_duration": duration}
                    self.current_sentence_words = split_highlight_units(self.get_sentences(c, p)[s])
                    self.current_sentence_duration = timing_info.get("speech_duration") or duration
                    self.current_word_start_time = asyncio.get_event_loop().time()
                    self.current_word_timings = timing_info.get("word_timings")
                    self.current_word_mapping = timing_info.get("word_mapping")
                elif command_name == 'click_jump':
                    if clicked_position := self._find_sentence_at_click(*data):
                        self.first_sentence_jump = False
                        self.chapter_idx, self.paragraph_idx, self.sentence_idx = clicked_position
                        self.ui_chapter_idx, self.ui_paragraph_idx, self.ui_sentence_idx = clicked_position
                        self.auto_scroll_enabled = False
                        self._save_extended_progress(sync_audio_position=True)
                        self.pending_restart_task = asyncio.create_task(self._restart_audio_after_navigation())
                elif command_name == 'chapter_click':
                    x, y = data
                    if self.chapter_index_panel_x + 1 <= x <= self.chapter_index_panel_x + self.chapter_index_panel_width and \
                       self.chapter_index_panel_y + 1 <= y <= self.chapter_index_panel_y + self.chapter_index_panel_height:
                        row = y - self.chapter_index_panel_y - 3
                        if row >= 0 and 0 <= (target_chap := self.chapter_index_scroll_offset + row) < len(self.chapters):
                            await self._jump_to_chapter(target_chap)
                continue
            
            if cmd == 'quit': break
            if cmd == 'finish':
                if self.player_task and not self.player_task.done(): await self.playback_finished_event.wait()
                await self.audio_queue.join()
                break

            elif cmd == '_resize':
                self.resize_scheduled = False
                if self.speed_reading_enabled:
                    self._layout_needs_update = True
                    asyncio.create_task(ui.display_ui(self))
                    continue
                if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
                
                _, height = ui.get_terminal_size()
                available_height = max(1, height - 4)
                max_scroll = max(0, len(self.document_lines) - available_height)
                old_scroll_percentage = self.scroll_offset / max_scroll if max_scroll > 0 else 0
                
                ui.update_document_layout(self)
                
                if not self.auto_scroll_enabled and self.resize_anchor:
                    anchor_pos, fraction_in_view = self.resize_anchor
                    if anchor_pos in self.position_to_line:
                        target_line = self.position_to_line[anchor_pos]
                        _, new_height = ui.get_terminal_size()
                        new_available_height = max(1, new_height - 4)
                        self.scroll_offset = max(0, target_line - int(round(fraction_in_view * new_available_height)))
                        self.target_scroll_offset = self.scroll_offset
                    else:
                        _, new_height = ui.get_terminal_size()
                        new_available_height = max(1, new_height - 4)
                        new_max_scroll = max(0, len(self.document_lines) - new_available_height)
                        self.scroll_offset = old_scroll_percentage * new_max_scroll
                        self.target_scroll_offset = self.scroll_offset
                else:
                    _, new_height = ui.get_terminal_size()
                    new_available_height = max(1, new_height - 4)
                    new_max_scroll = max(0, len(self.document_lines) - new_available_height)
                    self.scroll_offset = old_scroll_percentage * new_max_scroll
                    self.target_scroll_offset = self.scroll_offset
                
                self.resize_anchor = None
                self._save_extended_progress()
            elif cmd == 'pause':
                if not self.tts_model: continue
                self.is_paused = not self.is_paused
                self._save_extended_progress()
                self.current_pause_toggle_task = asyncio.create_task(self._handle_pause_toggle())
            elif cmd in ['scroll_page_up', 'scroll_page_down']:
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_page_scroll_smooth(-1 if 'up' in cmd else 1)
                else: self._handle_page_scroll_immediate(-1 if 'up' in cmd else 1)
            elif cmd in ['scroll_up', 'wheel_scroll_up']:
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_scroll_up_smooth()
                else: self._handle_scroll_up_immediate()
            elif cmd in ['scroll_down', 'wheel_scroll_down']:
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_scroll_down_smooth()
                else: self._handle_scroll_down_immediate()
            elif cmd == 'toggle_auto_scroll':
                self.auto_scroll_enabled = not self.auto_scroll_enabled
                if self.auto_scroll_enabled:
                    if self.smooth_scroll_task and not self.smooth_scroll_task.done(): self.smooth_scroll_task.cancel()
                    self._scroll_to_position_immediate(self.chapter_idx, self.paragraph_idx, self.sentence_idx)
                self._save_extended_progress()
            elif cmd == 'cycle_ui_complexity':
                config.UI_MODE = (config.UI_MODE + 1) % 5
                self.split_view_enabled = (config.UI_MODE == 4)
                self.speed_reading_enabled = (config.UI_MODE == 3)
                if not self.speed_reading_enabled and not self.split_view_enabled:
                    if self._layout_needs_update: self._layout_needs_update = False
                    ui.update_document_layout(self)
                else: self._layout_needs_update = True
                self._save_extended_progress()
                asyncio.create_task(ui.display_ui(self))
            elif cmd == 'move_to_top_visible':
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_move_to_top_smooth()
                else: self._handle_move_to_top_immediate()
                self.pending_restart_task = asyncio.create_task(self._restart_audio_after_navigation())
            elif cmd == 'move_to_beginning':
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_move_to_beginning_smooth()
                else: self._handle_move_to_beginning_immediate()
            elif cmd == 'move_to_end':
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_move_to_end_smooth()
                else: self._handle_move_to_end_immediate()
            elif cmd == 'copy_selection':
                self._handle_copy_selection()
            elif cmd in ['increase_speed', 'decrease_speed']:
                if (self._increase_speed() if 'increase' in cmd else self._decrease_speed()):
                    asyncio.create_task(ui.display_ui(self))
                    if not self.is_paused and self.tts_model:
                        self.pending_restart_task = asyncio.create_task(self._restart_audio_after_navigation())
            elif cmd == 'toggle_sentence_highlight':
                config.SENTENCE_HIGHLIGHTING_ENABLED = not config.SENTENCE_HIGHLIGHTING_ENABLED
                asyncio.create_task(ui.display_ui(self))
            elif cmd == 'toggle_word_highlight':
                config.WORD_HIGHLIGHT_MODE = (config.WORD_HIGHLIGHT_MODE + 1) % 3
                asyncio.create_task(ui.display_ui(self))
            elif 'next' in cmd or 'prev' in cmd:
                mode = cmd.split('_')[1] if '_' in cmd else ''
                if mode == 'chapter' and len(self.chapters) <= 1: continue
                if config.SMOOTH_SCROLLING_ENABLED: self._handle_navigation_smooth(cmd)
                else: self._handle_navigation_immediate(cmd)
                self.pending_restart_task = asyncio.create_task(self._restart_audio_after_navigation())
                        
        await self._shutdown()