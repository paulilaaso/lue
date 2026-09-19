import os
import re
import zipfile
import xml.etree.ElementTree as ET
import fitz
import markdown
from docx import Document
from striprtf.striprtf import rtf_to_text
import subprocess
import string
from rich.console import Console
from typing import List, Tuple
from pathlib import Path
from html.parser import HTMLParser
from html import unescape
from urllib.parse import unquote


def split_into_sentences(paragraph: str) -> list[str]:
    """
    Splits a paragraph into sentences, intelligently handling common abbreviations and initials.
    """
    abbreviations = [
        "Mr", "Mrs", "Ms", "Dr", "Prof", "Rev", "Hon", "Jr", "Sr",
        "Cpl", "Sgt", "Gen", "Col", "Capt", "Lt", "Pvt",
        "vs", "viz", "etc", "eg", "ie",
        "Co", "Inc", "Ltd", "Corp",
        "St", "Ave", "Blvd"
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

    text = re.sub(r'(?:^|\s)[.!?]+(?=\s|$)', ' ', text)
    text = re.sub(r'[—–]', ', ', text)
    text = re.sub(r'(?<=\w)-(?=\w)', ' ', text)
    sanitized = re.sub(r"[^\w\s.,:'();?!。，！？；：、।॥-]", '', text, flags=re.UNICODE)
    sanitized = re.sub(r'\s+', ' ', sanitized)
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
    
    text = re.sub(r'\s*\.\s*\.\s*\.\s*(\.\s*)*', '...', text)  
    text = re.sub(r'\s*\.\s*\.\s*(?!\s*\.)', '..', text)  
    text = re.sub(r'\.{4,}', '...', text)  
    
    text = re.sub(r'[-_=~`^]{3,}', '', text)           
    text = re.sub(r'[*]{4,}', '', text)                
    text = re.sub(r'[#]{4,}', '', text)                
    text = re.sub(r'[+]{3,}', '', text)                
    text = re.sub(r'[|]{3,}', '', text)                
    text = re.sub(r'[\\]{3,}', '', text)               
    text = re.sub(r'[/]{3,}', '', text)                
    
    unicode_replacements = {
        '×': ' multiplied by ', '÷': ' divided by ', '±': ' plus or minus ',
        '≤': ' less than or equal to ', '≥': ' greater than or equal to ', '≠': ' not equal to ',
        '≈': ' approximately' , '∞': 'infinity ', '%': ' percent ', '+': ' plus ', '=': ' equals ',
        '°': ' degrees ', '™': ' trademark ', '®': ' registered ',
        '©': ' copyright ', '§': ' section ',
        "’": "'",
        '\u200b': '', '\u200c': '', '\u200d': '',  
        '\ufeff': '',  
        '\u00ad': '',  
    }
    
    for old_char, new_char in unicode_replacements.items():
        text = text.replace(old_char, new_char)
    
    text = re.sub(r'\.{4,}', '...', text)  
    text = re.sub(r'…+', '...', text)      
    
    text = re.sub(r'\s+', ' ', text)  
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  
    
    text = re.sub(r'\.\.\.(?=\S)', '... ', text)  
    
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  
    text = re.sub(r'\*([^*]+)\*', r'\1', text)      
    text = re.sub(r'__([^_]+)__', r'\1', text)      
    text = re.sub(r'_([^_]+)_', r'\1', text)        
    text = re.sub(r'`([^`]+)`', r'\1', text)        
    text = re.sub(r'~~([^~]+)~~', r'\1', text)      
    
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  
    text = re.sub(r'\[([^\]]+)\]\[[^\ ]*\]', r'\1', text)  
    text = re.sub(r'^\s*\[[^\ ]+\]:\s*\S+.*$', '', text, flags=re.MULTILINE)  
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  
    
    text = re.sub(r'\s+([,!?;:])', r'\1', text)  
    text = re.sub(r'([,!?;:])\s*([,!?;:])', r'\1 \2', text)  
    
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
        self.heading_levels = {}  # index_ligne -> niveau
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
            self.current_heading_level = int(tag[1])  # h1 -> 1, h2 -> 2, etc.
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
                line = unescape(re.sub(r"\s+", " ", tmp))
            self.text[-1] += line
            if self.ishead:
                self.idhead.add(len(self.text)-1)
                self.heading_levels[len(self.text)-1] = self.current_heading_level
                self.ishead = False  # Reset immediately
            elif self.isbull:
                self.idbull.add(len(self.text)-1)
            elif self.inde:
                self.idinde.add(len(self.text)-1)
            elif self.ispref:
                self.idpref.add(len(self.text)-1)

    def get_lines(self):
        """Get clean text lines with proper formatting for different content types"""
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
                        clean_lines.append(f"__H{level}__ {line}")  # TAG: __H1__, __H2__, etc.
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
        line = re.sub(r'\s+', ' ', line).strip()
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
        return []
    
    try:
        container_data = zip_archive.read('META-INF/container.xml')
        container_root = ET.fromstring(container_data)
        rootfile_elem = container_root.find('.//{*}rootfile')
        if rootfile_elem is None:
            rootfile_elem = container_root.find('.//rootfile')
        
        if rootfile_elem is None:
            zip_archive.close()
            return []
            
        opf_path = rootfile_elem.get('full-path')
        if not opf_path:
            zip_archive.close()
            return []
        
        opf_path = opf_path.replace('\\', '/')
        opf_dir = os.path.dirname(opf_path) + "/" if os.path.dirname(opf_path) != "" else ""
        
        try:
            opf_data = zip_archive.read(opf_path)
        except KeyError:
            zip_archive.close()
            return []
            
        opf_root = ET.fromstring(opf_data)
        NS = {"OPF": "http://www.idpf.org/2007/opf"}
        
        manifest = {}
        try:
            for item in opf_root.findall(".//OPF:manifest/OPF:item", NS):
                if item.get("media-type") != "application/x-dtbncx+xml" and item.get("properties") != "nav":
                    manifest[item.get("id")] = opf_dir + unquote(item.get("href"))
        except:
            for item in opf_root.findall(".//manifest/item"):
                if item.get("media-type") != "application/x-dtbncx+xml" and item.get("properties") != "nav":
                    manifest[item.get("id")] = opf_dir + unquote(item.get("href"))
        
        contents = []
        try:
            spine_items = opf_root.findall(".//OPF:spine/OPF:itemref", NS)
        except:
            spine_items = opf_root.findall(".//spine/itemref")
            
        for spine_item in spine_items:
            item_id = spine_item.get("idref")
            if item_id in manifest:
                contents.append(manifest[item_id])
        
        if not contents:
            zip_archive.close()
            return []
        
        chapters = []
        for content_path in contents:
            try:
                content_data = zip_archive.read(content_path)
                content_str = content_data.decode('utf-8', errors='ignore')
            except:
                try:
                    content_str = content_data.decode('latin-1', errors='ignore')
                except:
                    continue
            
            parser = HTMLtoLines()
            try:
                parser.feed(content_str)
                parser.close()
            except:
                continue
            
            lines = parser.get_lines()
            if lines:
                chapters.append(lines)
        
        zip_archive.close()
        return chapters
    except Exception as e:
        try:
            zip_archive.close()
        except:
            pass
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
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except:
            return []
    except:
        return []

    content = content.replace('\r\n', '\n')
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
        return []


def _extract_content_md(file_path, console):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                md_content = f.read()
        except:
            return []
    except:
        return []

    try:
        return [_parse_raw_markdown(md_content)]
    except:
        try:
            html_content = markdown.markdown(md_content, extensions=['codehilite', 'fenced_code'])
            parser = HTMLtoLines()
            parser.feed(html_content)
            parser.close()
            lines = parser.get_lines()
            return [lines] if lines else []
        except:
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
                result.append("")  # Ligne vide avant
                result.append(f"__H{level}__ {header_text}")  # TAG: __H1__, __H2__, etc.
                result.append("")  # Ligne vide après
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
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except:
            return []
    except:
        return []

    try:
        parser = HTMLtoLines()
        parser.feed(content)
        parser.close()
        lines = parser.get_lines()
        return [lines] if lines else []
    except Exception as e:
        console.print(f"[bold red]Error: Failed to parse HTML file: {e}[/bold red]")
        return []
