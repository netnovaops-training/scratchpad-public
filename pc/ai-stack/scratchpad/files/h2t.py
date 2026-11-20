import html
import re
import sys
from pathlib import Path

t=Path(sys.argv[1]).read_text(encoding='utf-8')
# 2026-11-20: tags matched case-insensitively and the closing tag may carry spaces/attributes (</SCRIPT >), so such blocks no longer slip through
t=re.sub(r'(?is)<style\b.*?</style[^>]*>','',t);t=re.sub(r'(?is)<script\b.*?</script[^>]*>','',t)
t=re.sub(r'<(br|/p|/li|/h\d|/div|/tr)[^>]*>','\n',t);t=re.sub(r'<a [^>]*href="([^"]*)"[^>]*>',r'[\1] ',t);t=re.sub(r'<[^>]+>','',t);t=html.unescape(t)
sys.stdout.reconfigure(encoding='utf-8');print(re.sub(r'\n\s*\n+','\n',t))
