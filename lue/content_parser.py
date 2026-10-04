"""
Module de nettoyage et d'extraction de contenu pour différents formats de fichiers (EPUB, PDF, TXT, etc.).
Version nettoyée sans importations superflues, sans régression.
"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET
import fitz
import markdown
from docx import Document
from striprtf.striprtf import rtf_to_text
from html.parser import HTMLParser
from html import unescape
from urllib.parse import unquote
import logging

logger = logging.getLogger(__name__)


# ==========================================
# OPTIMISATIONS P1 (PATTERNS & TABLES COMPILÉS)
# ==========================================

UNICODE_TRANSLATION_TABLE = str.maketrans({
    '×': ' multiplied by ', '÷': ' divided by ', '±': ' plus or minus ',
    '≤': ' less than or equal to ', '≥': ' greater than or equal to ', '≠': ' not equal to ',
    '≈': ' approximately', '∞': 'infinity ', '%': ' percent ', '+': ' plus ', '=': ' equals ',
    '°': ' degrees ', '™': ' trademark ', '®': ' registered ',
    '©': ' copyright ', '§': ' section ',
    "’": "'",
    '\u200b': '', '\u200c': '', '\u200d': '',  
    '\ufeff': '',  
    '\u00ad': '',  
})

VISUAL_PATTERNS = {
    'ellipsis_multi': re.compile(r'\s*\.\s*\.\s*\.\s*(\.\s*)*'),
    'double_dot': re.compile(r'\s*\.\s*\.\s*(?!\s*\.)'),
    'many_dots': re.compile(r'\.{4,}'),
    'decoration': re.compile(r'[-_=~`^]{3,}'),
    'stars': re.compile(r'[*]{4,}'),
    'hash': re.compile(r'[#]{4,}'),
    'plus': re.compile(r'[+]{3,}'),
    'pipe': re.compile(r'[|]{3,}'),
    'backslash': re.compile(r'[\\]{3,}'),
    'slash': re.compile(r'[/]{3,}'),
    'unicode_ellipsis': re.compile(r'…+'),
    'ellipsis_space': re.compile(r'\.\.\.(?=\S)'),
    'newlines': re.compile(r'\n\s*\n\s*\n+'),
    'bold': re.compile(r'\*\*([^*]+)\*\*'),
    'italic': re.compile(r'\*([^*]+)\*'),
    'underline_bold': re.compile(r'__([^_]+)__'),
    'underline': re.compile(r'_([^_]+)_'),
    'code': re.compile(r'`([^`]+)`'),
    'strike': re.compile(r'~~([^~]+)~~'),
    'md_link': re.compile(r'\[([^\]]+)\]\([^)]+\)'),
    'md_ref': re.compile(r'\[([^\]]+)\]\[[^\ ]*\]'),
    'md_ref_def': re.compile(r'^\s*\[[^\ ]+\]:\s*\S+.*$', re.MULTILINE),
    'md_heading': re.compile(r'^#{1,6}\s+', re.MULTILINE),
    'punct_space': re.compile(r'\s+([,!?;:])'),
    'double_punct': re.compile(r'([,!?;:])\s*([,!?;:])'),
}

WHITESPACE_PATTERN = re.compile(r'\s+')
UNWANTED_TAGS_PATTERN = re.compile(r'<[^>]+>')


def strip_unwanted_tags(text: str) -> str:
    """Supprime toutes les balises XML/HTML/SSML résiduelles avant la lecture audio."""
    if not isinstance(text, str):
        return ""
    cleaned = UNWANTED_TAGS_PATTERN.sub('', text)
    return cleaned.strip()

def _normalize_raw_text(content: str) -> str:
    """
    Normalise le texte brut dès l'importation : applique la table de traduction 
    Unicode pour supprimer les caractères invisibles et normalise les sauts de ligne.
    """
    if not content:
        return ""
    content = content.translate(UNICODE_TRANSLATION_TABLE)
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    content = VISUAL_PATTERNS['newlines'].sub('\n\n', content)
    return content
    
    
def split_into_sentences(paragraph: str) -> list[str]:
    """
    Splits a paragraph into sentences, intelligently handling common abbreviations and initials.
    """
    abbreviations = [
        "Mr", "Mrs", "Ms", "Dr", "Prof", "Rev", "Hon", "Jr", "Sr",
        "Cpl", "Sgt", "Gen", "Col", "Capt", "Lt", "Pvt",
        "vs", "viz", "etc", "eg", "ie",
        "Co", "Inc", "Ltd", "Corp",
        "St", "Ave", "Blvd",
        # Français (« M. Dupont » est déjà couvert par la règle des initiales)
        "Mme", "Mmes", "Mlle", "Mlles", "Pr", "Ste", "cf", "chap", "fig",
        "av", "apr", "éd",
    ]
    
    abbrev_pattern = r"\b(" + "|".join(abbreviations) + r")\."
    placeholder = "<LUE_PERIOD>"
    
    paragraph = re.sub(abbrev_pattern, r"\1" + placeholder, paragraph, flags=re.IGNORECASE)
    initial_pattern = r"\b([A-Z])\.(?=\s[A-Z])"
    paragraph = re.sub(initial_pattern, r"\1" + placeholder, paragraph)
    
    spaced_punc = r'[.!?।॥]'
    cn_jp_punc = r'[。！？]'
    quotes = r'[”」』’"\']'
    split_pattern = (
        rf'(?<={spaced_punc})\s+|'                              
        rf'(?<={cn_jp_punc})(?!{cn_jp_punc}|{quotes})\s*|'       
        rf'(?<={cn_jp_punc}{quotes})(?!{cn_jp_punc})\s*|'        
        rf'(?<={cn_jp_punc}{quotes}{{2}})(?!{cn_jp_punc})\s*'    
    )
    sentences = re.split(split_pattern, paragraph)
    
    restored_sentences = []
    for sentence in sentences:
        if sentence:
            restored = sentence.replace(placeholder, ".")
            if restored:
                restored_sentences.append(restored)
                
    return restored_sentences if restored_sentences else [paragraph]


def sanitize_text_for_tts(text):
    """
    Sanitize text for TTS engines by removing special characters while preserving
    letters (including accented characters), numbers, and basic punctuation.
    """
    if not text or not isinstance(text, str):
        return ""

    # « vous ? » (typographie française) : on rattache ?/! au mot précédent au
    # lieu de les supprimer ; seuls les points isolés (artefacts) disparaissent.
    text = re.sub(r'(?:^|\s)(?!\.{2,})\.+(?=\s|$)', ' ', text)
    text = re.sub(r'\s+([?!]+)(?=[\s»”)\]"\']|$)', r'\1', text)
    text = re.sub(r'^\s*[?!]+(?=\s|$)\s*', '', text)
    text = re.sub(r'[—–]', ', ', text)
    text = re.sub(r'(?<=\w)-(?=\w)', ' ', text)
    sanitized = re.sub(r"[^\w\s.,:'();?!。，！？；：、।॥-]", '', text, flags=re.UNICODE)
    sanitized = WHITESPACE_PATTERN.sub(' ', sanitized)
    return sanitized.strip()


def prepare_text_for_tts(text: str) -> str:
    """Compatibility helper for TTS preparation."""
    return sanitize_text_for_tts(text)


def clean_visual_text(text):
    """
    Clean text for visual display in the UI.
    """
    if not text or not isinstance(text, str):
        return text
    
    if text.startswith('__CODE_BLOCK__'):
        code_content = text[14:]  
        return code_content
    
    text = VISUAL_PATTERNS['ellipsis_multi'].sub('...', text)  
    text = VISUAL_PATTERNS['double_dot'].sub('..', text)  
    text = VISUAL_PATTERNS['many_dots'].sub('...', text)  
    
    text = VISUAL_PATTERNS['decoration'].sub('', text)           
    text = VISUAL_PATTERNS['stars'].sub('', text)                
    text = VISUAL_PATTERNS['hash'].sub('', text)                
    text = VISUAL_PATTERNS['plus'].sub('', text)                
    text = VISUAL_PATTERNS['pipe'].sub('', text)                
    text = VISUAL_PATTERNS['backslash'].sub('', text)               
    text = VISUAL_PATTERNS['slash'].sub('', text)                
    
    text = text.translate(UNICODE_TRANSLATION_TABLE)
    
    text = VISUAL_PATTERNS['many_dots'].sub('...', text)  
    text = VISUAL_PATTERNS['unicode_ellipsis'].sub('...', text)      
    
    text = WHITESPACE_PATTERN.sub(' ', text)  
    text = VISUAL_PATTERNS['newlines'].sub('\n\n', text)  
    
    text = VISUAL_PATTERNS['ellipsis_space'].sub('... ', text)  
    
    text = VISUAL_PATTERNS['bold'].sub(r'\1', text)  
    text = VISUAL_PATTERNS['italic'].sub(r'\1', text)      
    text = VISUAL_PATTERNS['underline_bold'].sub(r'\1', text)      
    text = VISUAL_PATTERNS['underline'].sub(r'\1', text)        
    text = VISUAL_PATTERNS['code'].sub(r'\1', text)        
    text = VISUAL_PATTERNS['strike'].sub(r'\1', text)      
    
    text = VISUAL_PATTERNS['md_link'].sub(r'\1', text)  
    text = VISUAL_PATTERNS['md_ref'].sub(r'\1', text)  
    text = VISUAL_PATTERNS['md_ref_def'].sub('', text)  
    text = VISUAL_PATTERNS['md_heading'].sub('', text)  
    
    text = VISUAL_PATTERNS['punct_space'].sub(r'\1', text)  
    text = VISUAL_PATTERNS['double_punct'].sub(r'\1 \2', text)  
    
    return text.strip()


class HTMLtoLines(HTMLParser):
    """HTML parser"""
    para = {"p", "div"}
    inde = {"q", "dt", "dd", "blockquote"}
    pref = {"pre"}
    bull = {"li"}
    hide = {"script", "style", "head"}

    def __init__(self):
        HTMLParser.__init__(self)
        self.text = [""]
        self.imgs = []
        self.ishead = False
        self.current_heading_level = 0
        self.heading_levels = {}  
        self.isinde = False
        self.isbull = False
        self.ispref = False
        self.ishidden = False
        self.idhead = set()
        self.idinde = set()
        self.idbull = set()
        self.idpref = set()
        self.hiding_tags = []  
        self._current_span_attrs = {}

    def handle_starttag(self, tag, attrs):
        if re.match("h[1-6]", tag) is not None:
            self.ishead = True
            self.current_heading_level = int(tag[1])  
        elif tag in self.inde:
            self.isinde = True
        elif tag in self.pref:
            self.ispref = True
        elif tag in self.bull:
            self.isbull = True
        elif tag in self.hide:
            self.ishidden = True
            self.hiding_tags.append(tag)
        elif tag == "sup":
            self.ishidden = True
            self.hiding_tags.append("sup")
        elif tag == "sub":
            self.ishidden = True
            self.hiding_tags.append("sub")
        elif tag == "span":
            self._current_span_attrs = dict(attrs)
            span_class = self._current_span_attrs.get('class', '')
            if (len(span_class) <= 3 and  
                (span_class.isdigit() or  
                 span_class in {'su', 'sit', 'bs', 'is', 'fn', 'note', 'ref'} or  
                 'footnote' in span_class.lower() or
                 'note' in span_class.lower() or
                 'ref' in span_class.lower())):
                self.ishidden = True
                self.hiding_tags.append("span")
        elif tag in {"img", "image"}:
            pass

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.text += [""]
        elif tag in {"img", "image"}:
            pass

    def handle_endtag(self, tag):
        if re.match("h[1-6]", tag) is not None:
            self.text.append("")
            self.text.append("")
            self.ishead = False
        elif tag in self.para:
            self.text.append("")
        elif tag in self.hide:
            if self.hiding_tags and self.hiding_tags[-1] == tag:
                self.hiding_tags.pop()
                self.ishidden = len(self.hiding_tags) > 0
        elif tag in self.inde:
            if self.text[-1] != "":
                self.text.append("")
            self.isinde = False
        elif tag in self.pref:
            if self.text[-1] != "":
                self.text.append("")
            self.ispref = False
        elif tag in self.bull:
            if self.text[-1] != "":
                self.text.append("")
            self.isbull = False
        elif tag == "sup":
            if self.hiding_tags and self.hiding_tags[-1] == "sup":
                self.hiding_tags.pop()
                self.ishidden = len(self.hiding_tags) > 0
        elif tag == "sub":
            if self.hiding_tags and self.hiding_tags[-1] == "sub":
                self.hiding_tags.pop()
                self.ishidden = len(self.hiding_tags) > 0
        elif tag == "span":
            if self.hiding_tags and self.hiding_tags[-1] == "span":
                self.hiding_tags.pop()
                self.ishidden = len(self.hiding_tags) > 0
        elif tag in {"img", "image"}:
            pass

    def handle_data(self, raw):
        if raw and not self.ishidden:
            if self.text[-1] == "":
                tmp = raw.lstrip()
            else:
                tmp = raw
            if self.ispref:
                line = unescape(tmp)
            else:
                line = unescape(WHITESPACE_PATTERN.sub(" ", tmp))
            self.text[-1] += line
            if self.ishead:
                self.idhead.add(len(self.text)-1)
                self.heading_levels[len(self.text)-1] = self.current_heading_level
                self.ishead = False  
            elif self.isbull:
                self.idbull.add(len(self.text)-1)
            elif self.inde:
                self.idinde.add(len(self.text)-1)
            elif self.ispref:
                self.idpref.add(len(self.text)-1)

    def get_lines(self):
        clean_lines = []
        for i, line in enumerate(self.text):
            line = line.strip()
            if line and len(line) > 3:  
                line = self._clean_line(line)
                line = clean_visual_text(line)
                if line and len(line) > 3:  
                    if i in self.idhead:
                        level = self.heading_levels.get(i, 1)
                        clean_lines.append("")
                        clean_lines.append(f"__H{level}__ {line}")  
                        clean_lines.append("")
                    elif i in self.idbull:
                        clean_lines.append(f"• {line}")
                    elif i in self.idinde:
                        clean_lines.append(f"    {line}")
                    elif i in self.idpref:
                        clean_lines.append(f"    {line}")
                    else:
                        clean_lines.append(line)
        
        result = []
        empty_count = 0
        for line in clean_lines:
            if line == "":
                empty_count += 1
                if empty_count <= 2:  
                    result.append(line)
            else:
                empty_count = 0
                result.append(line)
        
        return result
    
    def _is_footnote_reference(self, content):
        if not content or len(content.strip()) == 0:
            return False
        content = content.strip()
        if len(content) <= 3:
            if re.match(r'^\d+$', content):
                return True
            if re.match(r'^[*†‡§¶]+$', content):
                return True
            if re.match(r'^[a-zA-Z]$', content):
                return True
        if len(content) <= 5:
            if re.match(r'^\d+[.,;:]?$', content):
                return True
            if re.match(r'^[ivxlcdm]+$', content.lower()):
                return True
        return False

    def _clean_line(self, line):
        line = re.sub(r'\^{[^}]*}', '', line)
        line = re.sub(r'_{[^}]*}', '', line)
        line = re.sub(r'\[IMG:\d+\]', '', line)
        line = re.sub(r'\[\d+\]', '', line)
        line = re.sub(r'\[[a-zA-Z]+\d*\]', '', line)
        line = re.sub(r'[*†‡§¶]+', '', line)
        line = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', line)
        line = WHITESPACE_PATTERN.sub(' ', line).strip()
        return line


def extract_content(file_path, console):
    file_extension = os.path.splitext(file_path)[1].lower()
    if file_extension == '.epub':
        return _extract_content_epub(file_path, console)
    elif file_extension == '.pdf':
        return _extract_content_pdf(file_path, console)
    elif file_extension == '.txt':
        return _extract_content_txt(file_path, console)
    elif file_extension == '.docx':
        return _extract_content_docx(file_path, console)
    elif file_extension == '.html':
        return _extract_content_html(file_path, console)
    elif file_extension == '.rtf':
        return _extract_content_rtf(file_path, console)
    elif file_extension == '.md':
        return _extract_content_md(file_path, console)
    else:
        console.print(f"[bold red]Error: Unsupported file type '{file_extension}'.[/bold red]")
        return []


def _extract_content_epub(file_path, console):
    try:
        zip_archive = zipfile.ZipFile(file_path, 'r')
    except Exception as e:
        console.print(f"[bold red]Error: Failed to open EPUB file as ZIP: {e}[/bold red]")
        logger.error("Failed to open EPUB file as ZIP", exc_info=e)
        return []
    
    try:
        container_data = zip_archive.read('META-INF/container.xml')
        container_root = ET.fromstring(container_data)
        rootfile_elem = container_root.find('.//{*}rootfile')
        if rootfile_elem is None:
            rootfile_elem = container_root.find('.//rootfile')
        
        if rootfile_elem is None:
            logger.warning("Rootfile element not found in container.xml")
            zip_archive.close()
            return []
            
        opf_path = rootfile_elem.get('full-path')
        if not opf_path:
            logger.warning("OPF path attribute is empty")
            zip_archive.close()
            return []
        
        opf_path = opf_path.replace('\\', '/')
        opf_dir = os.path.dirname(opf_path) + "/" if os.path.dirname(opf_path) != "" else ""
        
        try:
            opf_data = zip_archive.read(opf_path)
        except KeyError as ke:
            logger.error("OPF path not found in zip archive: %s", opf_path, exc_info=ke)
            zip_archive.close()
            return []
            
        opf_root = ET.fromstring(opf_data)
        NS = {"OPF": "http://www.idpf.org/2007/opf"}
        
        manifest = {}
        try:
            for item in opf_root.findall(".//OPF:manifest/OPF:item", NS):
                if item.get("media-type") != "application/x-dtbncx+xml" and item.get("properties") != "nav":
                    manifest[item.get("id")] = opf_dir + unquote(item.get("href"))
        except Exception as e:
            logger.debug("Failed to find manifest items with OPF namespace, falling back", exc_info=e)
            for item in opf_root.findall(".//manifest/item"):
                if item.get("media-type") != "application/x-dtbncx+xml" and item.get("properties") != "nav":
                    manifest[item.get("id")] = opf_dir + unquote(item.get("href"))
        
        contents = []
        try:
            spine_items = opf_root.findall(".//OPF:spine/OPF:itemref", NS)
        except Exception as e:
            logger.debug("Failed to find spine items with OPF namespace, falling back", exc_info=e)
            spine_items = opf_root.findall(".//spine/itemref")
            
        for spine_item in spine_items:
            item_id = spine_item.get("idref")
            if item_id in manifest:
                contents.append(manifest[item_id])
        
        if not contents:
            logger.warning("No valid content items found in spine")
            zip_archive.close()
            return []
        
        chapters = []
        for content_path in contents:
            try:
                content_data = zip_archive.read(content_path)
                content_str = content_data.decode('utf-8', errors='ignore')
            except Exception as utf_err:
                logger.debug("Failed to decode chapter with utf-8, trying latin-1", exc_info=utf_err)
                try:
                    content_str = content_data.decode('latin-1', errors='ignore')
                except Exception as lat_err:
                    logger.error("Failed to decode chapter content for path: %s", content_path, exc_info=lat_err)
                    continue
            
            parser = HTMLtoLines()
            try:
                parser.feed(content_str)
                parser.close()
            except Exception as parse_err:
                logger.error("Failed to parse HTML for chapter path: %s", content_path, exc_info=parse_err)
                continue
            
            lines = parser.get_lines()
            if lines:
                chapters.append(lines)
        
        zip_archive.close()
        return chapters
    except Exception as e:
        logger.error("Unexpected error during EPUB content extraction", exc_info=e)
        try:
            zip_archive.close()
        except Exception as close_err:
            logger.warning("Failed to close zip archive safely", exc_info=close_err)
        return []


def _extract_content_pdf(file_path, console):
    from . import config
    
    def is_footnote_block(block, page_height, bottom_margin):
        x0, y0, x1, y1, text = block[:5]
        text = text.strip()
        if y0 < page_height * (1 - bottom_margin):
            return False
        if text:
            if len(text) < 20:
                return True
            if re.match(r'^\d+$', text):
                return True
            if re.match(r'^Page\s+\d+', text, re.IGNORECASE):
                return True
            if re.match(r'^\d+\s*[-–—]\s*\d+$', text):
                return True
            if re.match(r'^\d+\s*/\s*\d+$', text):
                return True
            if re.match(r'^\d+[\.\s]', text):
                return True
            if re.match(r'^[*†‡§¶]', text):
                return True
            if len(text) < 100 and any(word in text.lower() for word in ['chapter', 'page', 'copyright', '©']):
                return True
        return False

    def is_header_block(block, page_height, top_margin):
        block_y0 = block[1]
        return block_y0 < page_height * top_margin

    def clean_footnote_references(text):
        cleaned = re.sub(r'\[\d+\]', '', text)
        cleaned = re.sub(r'\[[a-zA-Z]\]', '', cleaned)
        cleaned = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', cleaned)
        cleaned = re.sub(r'[*†‡§¶]', '', cleaned)
        cleaned = re.sub(r'^\d+\.\s', '', cleaned)
        return ' '.join(cleaned.split())

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        console.print(f"[bold red]Error: Failed to open file with fitz: {e}[/bold red]")
        logger.error("Failed to open PDF file with fitz", exc_info=e)
        return []

    all_paragraphs = []
    for page in doc:
        page_height = page.rect.height
        main_content_blocks = []
        blocks = sorted(page.get_text("blocks"), key=lambda b: b[1])

        for block in blocks:
            if config.PDF_FILTERS_ENABLED and config.PDF_FILTER_FOOTNOTES and is_footnote_block(block, page_height, config.PDF_FOOTNOTE_MARGIN):
                continue
            if config.PDF_FILTERS_ENABLED and config.PDF_FILTER_HEADERS and is_header_block(block, page_height, config.PDF_HEADER_MARGIN):
                continue
            main_content_blocks.append(block)

        page_text = ""
        for block in main_content_blocks:
            block_text = block[4].replace('-\n', '').replace('\n', ' ').strip()
            page_text += block_text + " "
        
        cleaned_page_text = clean_footnote_references(page_text)
        paragraphs = cleaned_page_text.split('  ')
        
        for para in paragraphs:
            if len(para.strip()) > 25:
                cleaned_para = clean_visual_text(para.strip())
                if cleaned_para and len(cleaned_para) > 10:
                    all_paragraphs.append(cleaned_para)

    doc.close() 
    
    chapters = []
    current_chapter = []
    for paragraph in all_paragraphs:
        if "chapter" in paragraph.lower() and len(paragraph.split()) < 10:
            if current_chapter:
                chapters.append(current_chapter)
            current_chapter = [paragraph]
        else:
            current_chapter.append(paragraph)
            
    if current_chapter:
        chapters.append(current_chapter)
        
    return chapters if chapters else [all_paragraphs]


def _extract_content_txt(file_path, console):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError as ude:
        logger.debug("Failed to read TXT with utf-8, trying latin-1", exc_info=ude)
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as lat_err:
            logger.error("Failed to read TXT file with latin-1 encoding", exc_info=lat_err)
            return []
    except Exception as e:
        logger.error("Failed to read TXT file", exc_info=e)
        return []

    # Normalisation immédiate dès l'importation[cite: 2]
    content = _normalize_raw_text(content)
    
    paragraphs = [clean_visual_text(p.strip()) for p in content.split('\n\n') if p.strip()]
    paragraphs = [p for p in paragraphs if p and len(p) > 3]
    
    if len(paragraphs) <= 1 and '\n' in content:
        paragraphs = [clean_visual_text(p.strip()) for p in content.split('\n') if p.strip()]
        paragraphs = [p for p in paragraphs if p and len(p) > 3]

    return [paragraphs] if paragraphs else []


def _extract_content_docx(file_path, console):
    try:
        doc = Document(file_path)
        full_text = "\n".join([para.text for para in doc.paragraphs if para.text and not para.text.isspace()])
        paragraphs = [clean_visual_text(p.strip()) for p in full_text.split('\n') if p.strip()]
        paragraphs = [p for p in paragraphs if p and len(p) > 3]
        return [paragraphs]
    except Exception as e:
        console.print(f"[bold red]Error: Failed to read DOCX file: {e}[/bold red]")
        logger.error("Failed to read DOCX file", exc_info=e)
        return []
        

def _extract_content_rtf(file_path, console):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            rtf_content = f.read()
        text_content = rtf_to_text(rtf_content, errors="ignore")
        text_content = text_content.replace('\r\n', '\n').replace('\r', '\n')
        lines = [clean_visual_text(line.strip()) for line in text_content.split('\n') if line.strip()]
        lines = [line for line in lines if line and len(line) > 3]
        return [lines]
    except Exception as e:
        console.print(f"[bold red]Error: Failed to parse RTF file: {e}[/bold red]")
        logger.error("Failed to parse RTF file", exc_info=e)
        return []


def _extract_content_md(file_path, console):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
    except UnicodeDecodeError as ude:
        logger.debug("Failed to read markdown with utf-8, trying latin-1", exc_info=ude)
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                md_content = f.read()
        except Exception as lat_err:
            logger.error("Failed to read Markdown file with latin-1 encoding", exc_info=lat_err)
            return []
    except Exception as e:
        logger.error("Failed to read Markdown file", exc_info=e)
        return []

    # Normalisation immédiate dès l'importation[cite: 2]
    md_content = _normalize_raw_text(md_content)

    try:
        return [_parse_raw_markdown(md_content)]
    except Exception as parse_err:
        logger.debug("Raw markdown parsing failed, falling back to markdown package parser", exc_info=parse_err)
        try:
            html_content = markdown.markdown(md_content, extensions=['codehilite', 'fenced_code'])
            parser = HTMLtoLines()
            parser.feed(html_content)
            parser.close()
            lines = parser.get_lines()
            return [lines] if lines else []
        except Exception as html_err:
            logger.error("Failed to parse markdown via HTML fallback parser", exc_info=html_err)
            return []


def _parse_raw_markdown(md_content):
    lines = md_content.split('\n')
    result = []
    in_code_block = False
    code_fence = None
    
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        
        if line.startswith('```') or line.startswith('~~~'):
            if not in_code_block:
                in_code_block = True
                code_fence = line[:3]
                result.append("")  
                lang = line[3:].strip()
                if lang:
                    result.append(f"Code ({lang}):")
                else:
                    result.append("Code:")
                i += 1
                continue
            elif line.startswith(code_fence):
                in_code_block = False
                code_fence = None
                result.append("")  
                i += 1
                continue
        
        if in_code_block:
            result.append(f"__CODE_BLOCK__    {line}")
        elif line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            header_text = line.lstrip('# ').strip()
            if header_text:
                result.append("")  
                result.append(f"__H{level}__ {header_text}")  
                result.append("")  
        elif line.startswith(('- ', '* ', '+ ')) or re.match(r'^\d+\.\s', line):
            if line.startswith(('- ', '* ', '+ ')):
                list_text = line[2:].strip()
                result.append(f"• {list_text}")
            else:
                list_text = re.sub(r'^\d+\.\s', '', line).strip()
                result.append(f"• {list_text}")
        elif re.match(r'^\s+(-|\*|\+|\d+\.)\s+', line):
            indented_list_text = re.sub(r'^\s+(-|\*|\+|\d+\.)\s+', '', line)
            result.append(f"    • {indented_list_text}")
        elif line.startswith('    ') or line.startswith('\t'):
            result.append(f"__CODE_BLOCK__    {line.strip()}")
        elif line.startswith('>'):
            quote_text = line.lstrip('> ').strip()
            if quote_text:
                result.append(f"    {quote_text}")
        elif line.strip() == '':
            if result and result[-1] != '':
                result.append('')
        else:
            if line.strip():
                result.append(line.strip())
        
        i += 1
    
    result = [
        clean_visual_text(line) if line.strip() and not line.startswith('__H') and not line.startswith('__CODE_BLOCK__')
        else line
        for line in result
    ]
    
    clean_result = []
    empty_count = 0
    for line in result:
        if line == '':
            empty_count += 1
            if empty_count <= 2:
                clean_result.append(line)
        else:
            empty_count = 0
            clean_result.append(line)
    
    return [line for line in clean_result if line or len(clean_result) < 100]


def _extract_content_html(file_path, console):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError as ude:
        logger.debug("Failed to read HTML with utf-8, trying latin-1", exc_info=ude)
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as lat_err:
            logger.error("Failed to read HTML file with latin-1 encoding", exc_info=lat_err)
            return []
    except Exception as e:
        logger.error("Failed to read HTML file", exc_info=e)
        return []

    # Normalisation immédiate dès l'importation[cite: 2]
    content = _normalize_raw_text(content)

    try:
        parser = HTMLtoLines()
        parser.feed(content)
        parser.close()
        lines = parser.get_lines()
        return [lines] if lines else []
    except Exception as e:
        console.print(f"[bold red]Error: Failed to parse HTML file: {e}[/bold red]")
        logger.error("Failed to parse HTML file via HTMLtoLines", exc_info=e)
        return []