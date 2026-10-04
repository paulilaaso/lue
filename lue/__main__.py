"""Main entry point for the Lue eBook reader application."""

import asyncio
import sys
import termios
import tty
import shutil
import argparse
import os
import platform
import platformdirs
import logging
try:
    from importlib.resources import files
except ImportError:
    # Fallback for Python < 3.9
    from importlib_resources import files
from rich.console import Console
from .reader import Lue
from . import config, progress_manager, input_handler
from .tts_manager import TTSManager, get_default_tts_model_name

def get_keyboard_shortcuts_file(keys_arg):
    """Resolve the keyboard shortcuts file path from the command line argument."""
    # If it's a direct file path that exists, use it
    if os.path.isfile(keys_arg):
        return keys_arg
    
    # If it's a preset name, look for keys_{name}.json in the lue directory
    preset_file = os.path.join(os.path.dirname(__file__), f'keys_{keys_arg}.json')
    if os.path.isfile(preset_file):
        return preset_file
    
    # If it's the special "default" name, use keys_default.json
    if keys_arg == "default":
        default_file = os.path.join(os.path.dirname(__file__), 'keys_default.json')
        if os.path.isfile(default_file):
            return default_file
    
    # Fallback to default
    return os.path.join(os.path.dirname(__file__), 'keys_default.json')

def get_guide_file_path():
    """Get the path to the guide file, creating a temporary file if needed for packaged installs."""
    import tempfile
    
    try:
        # Try to get the guide from the package data first (for pip installs)
        try:
            guide_file = files('lue') / 'guide.txt'
            guide_content = guide_file.read_text(encoding='utf-8')
            
            # Create a temporary file with a user-friendly name
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, "Lue Navigation Guide.txt")
            
            with open(temp_path, 'w', encoding='utf-8') as temp_file:
                temp_file.write(guide_content)
            
            return temp_path
            
        except (FileNotFoundError, ModuleNotFoundError):
            # Fallback to local file (for development)
            guide_path = os.path.join(os.path.dirname(__file__), 'guide.txt')
            if os.path.exists(guide_path):
                return guide_path
            else:
                return None
                
    except Exception:
        return None

def setup_logging():
    """Set up file-based logging for the application."""
    log_dir = platformdirs.user_log_dir(appname="lue", appauthor=False)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "error.log")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        filename=log_file,
        filemode='a',
        force=True,
    )
    logging.info("Application starting")

def setup_environment():
    """Set environment variables for TTS models."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_ETAG_TIMEOUT"] = "10"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "10"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    if platform.system() == "Darwin" and platform.processor() == "arm":
        os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

def preprocess_filter_args(args):
    """Preprocess arguments to handle --filter with space-separated values."""
    processed_args = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ['--filter', '-f']:
            # Found filter argument
            processed_args.append(arg)
            i += 1
            
            # Collect numeric values that follow
            filter_values = []
            while i < len(args):
                try:
                    # Try to parse as float
                    float(args[i])
                    filter_values.append(args[i])
                    i += 1
                    if len(filter_values) >= 2:  # Max 2 values
                        break
                except ValueError:
                    # Not a number, stop collecting
                    break
            
            # Join the values and add as a single argument
            if filter_values:
                processed_args.append(' '.join(filter_values))
            else:
                # No values provided, add empty string
                processed_args.append('')
        else:
            processed_args.append(arg)
            i += 1
    
    return processed_args

async def main():
    # Preprocess arguments to handle filter syntax
    preprocessed_args = preprocess_filter_args(sys.argv[1:])
    
    tts_manager = TTSManager()
    available_tts = tts_manager.get_available_tts_names()
    default_tts = get_default_tts_model_name(available_tts)

    parser = argparse.ArgumentParser(
        description="A terminal-based eBook reader with TTS",
        add_help=False  # Disable automatic help to add custom one
    )
    
    parser.add_argument(
        '-h', '--help',
        action='help',
        help='Show this help message and exit'
    )
    
    parser.add_argument(
        '-g', '--guide',
        action='store_true',
        help='Open the keyboard shortcuts navigation guide'
    )
    
    parser.add_argument("file_path", nargs='?', help="Path to the eBook file (.epub, .pdf, .txt, etc.). If not provided, opens the last book you were reading.")
    parser.add_argument(
        "-f",
        "--filter",
        nargs='?',
        const='',
        help="Enable PDF text cleaning filters. Usage: --filter (defaults), --filter 0.15 (both margins), --filter 0.12 0.20 (header, footnote)",
    )
    
    parser.add_argument(
        "-o", "--over", type=float, help="Seconds of overlap between sentences"
    )
    
    parser.add_argument(
        "-k", "--keys",
        help="Keyboard configuration. Use a preset name (vim, default) or a path to a JSON file. Default: default",
        default="default"
    )

    parser.add_argument(
        "-m", "--mode",
        type=int,
        choices=[0, 1, 2, 3, 4],
        help="Visual layout mode: 0=minimal, 1=medium, 2=full, 3=speed reading, 4=split view",
    )
    
    if available_tts:
        # Add "none" option to available TTS choices
        tts_choices = ["none"] + available_tts
        parser.add_argument(
            "-t",
            "--tts",
            choices=tts_choices,
            default=default_tts,
            help=f"Select the Text-to-Speech model. Use 'none' to disable TTS (default: {default_tts})",
        )
        parser.add_argument(
            "-v",
            "--voice",
            help="Specify the voice for the TTS model",
        )
        parser.add_argument(
            "-s",
            "--speed",
            type=float,
            default=1.0,
            help="Set the speech speed (default: 1.0)",
        )
        parser.add_argument(
            "-l",
            "--lang",
            help="Specify the language for the TTS model",
        )
    args = parser.parse_args(preprocessed_args)

    # Initialize console early for printing messages
    console = Console()

    # Handle guide argument - open guide file in Lue app
    if args.guide:
        guide_path = get_guide_file_path()
        if guide_path:
            console.print("[green]Opening navigation guide...[/green]")
            args.file_path = guide_path
        else:
            console.print("[red]Guide file not found.[/red]")
            sys.exit(1)
    # Handle the case when no file is provided - try to open the last book
    elif not args.file_path:
        last_book_path = progress_manager.find_most_recent_book()
        if last_book_path:
            console.print(f"[green]Opening last book: {os.path.basename(last_book_path)}[/green]")
            args.file_path = last_book_path
        else:
            console.print("[red]No file specified and no previous books found.[/red]")
            console.print("Please provide a file path as an argument.")
            parser.print_help()
            sys.exit(1)
    else:
        # Convert relative path to absolute path for consistency
        args.file_path = os.path.abspath(args.file_path)

    if args.over is not None:
        config.OVERLAP_SECONDS = args.over

    if args.mode is not None:
        config.UI_MODE = args.mode
        config.UI_MODE_OVERRIDE = True

    # Handle PDF filter settings
    if args.filter is not None:
        config.PDF_FILTERS_ENABLED = True
        
        if args.filter == '':
            # Just --filter with no values, use defaults
            pass
        else:
            # Parse the filter values
            try:
                filter_values = [float(x.strip()) for x in args.filter.split() if x.strip()]
                
                if len(filter_values) == 1:
                    # One number provided - set both margins to this value
                    config.PDF_HEADER_MARGIN = filter_values[0]
                    config.PDF_FOOTNOTE_MARGIN = filter_values[0]
                elif len(filter_values) == 2:
                    # Two numbers provided - set header and footnote margins separately
                    config.PDF_HEADER_MARGIN = filter_values[0]
                    config.PDF_FOOTNOTE_MARGIN = filter_values[1]
                elif len(filter_values) > 2:
                    console.print("[red]Error: --filter accepts at most 2 values (header margin, footnote margin)[/red]")
                    sys.exit(1)
            except ValueError:
                console.print(f"[red]Error: Invalid filter values '{args.filter}'. Expected float numbers.[/red]")
                sys.exit(1)

    setup_environment()
    setup_logging()


    # Verify that the external FFmpeg executables required by the application
    # are discoverable through PATH. This avoids spawning three processes merely
    # to run "-version".
    required_tools = ("ffmpeg", "ffprobe", "ffplay")
    missing_tools = [tool for tool in required_tools if shutil.which(tool) is None]

    if missing_tools:
        tools = ", ".join(missing_tools)
        console.print(
            f"\n[bold red]Error: {tools} not found.[/bold red] "
            "Please install FFmpeg and ensure its executables are in your system's PATH."
        )
        logging.error("Required FFmpeg tools not found: %s", tools)
        sys.exit(1)

    # Resolve keyboard shortcuts file
    # Prioritize command-line argument if explicitly provided (not the default)
    if args.keys != "default":
        # User explicitly provided a keys argument
        keyboard_shortcuts_file = get_keyboard_shortcuts_file(args.keys)
    elif config.CUSTOM_KEYBOARD_SHORTCUTS and config.CUSTOM_KEYBOARD_SHORTCUTS != "default":
        # Use the config value if it's not the default
        keyboard_shortcuts_file = get_keyboard_shortcuts_file(config.CUSTOM_KEYBOARD_SHORTCUTS)
    else:
        # Fall back to default
        keyboard_shortcuts_file = get_keyboard_shortcuts_file("default")
    
    # Load keyboard shortcuts
    input_handler.load_keyboard_shortcuts(keyboard_shortcuts_file)
    
    tts_instance = None
    if available_tts and hasattr(args, 'tts') and args.tts and args.tts != "none":
        voice = args.voice if hasattr(args, 'voice') else None
        lang = args.lang if hasattr(args, 'lang') else None
        tts_instance = tts_manager.create_model(args.tts, console, voice=voice, lang=lang)

    reader = Lue(args.file_path, tts_model=tts_instance, overlap=args.over)
    if hasattr(args, 'speed'):
        reader.playback_speed = args.speed
        
    # Hide cursor, enable mouse tracking
    sys.stdout.write('\033[?1000h\033[?1006h\033[?25l')
    sys.stdout.flush()
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    temp_guide_file = None
    
    # Check if we're using a temporary guide file
    if args.guide and args.file_path and "Lue Navigation Guide.txt" in args.file_path:
        temp_guide_file = args.file_path
    
    try:
        tty.setcbreak(sys.stdin.fileno())
        
        initialized = await reader.initialize_tts()
        if not initialized and hasattr(args, 'tts') and args.tts and args.tts != "none":
            console.print(f"[bold yellow]Warning: TTS model '{args.tts}' "
                         "failed to initialize and has been disabled.[/bold yellow]")

        # Switch to alternate screen buffer just before running the UI
        sys.stdout.write('\033[?1049h')
        sys.stdout.flush()

        await reader.run()

    finally:
        sys.stdout.write('\033[?1049l\033[?1000l\033[?1006l\033[?25h')
        sys.stdout.flush()
        if fd is not None and old_settings is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        
        # Clean up temporary guide file if it was created
        if temp_guide_file:
            try:
                os.unlink(temp_guide_file)
            except (OSError, FileNotFoundError):
                pass

def cli():
    """Synchronous entry point for the command-line interface."""
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logging.critical(f"Fatal error in application startup: {e}", exc_info=True)

if __name__ == "__main__":
    cli()
