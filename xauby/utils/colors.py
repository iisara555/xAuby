import re

def fg_rgb(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"

def bg_rgb(r: int, g: int, b: int) -> str:
    return f"\033[48;2;{r};{g};{b}m"

def clamp_rgb(value: int) -> int:
    return max(0, min(255, int(value)))

def blend_rgb(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Blend two RGB colors; t=0 returns start, t=1 returns end."""
    mix = max(0.0, min(1.0, float(t)))
    return (
        clamp_rgb(start[0] + (end[0] - start[0]) * mix),
        clamp_rgb(start[1] + (end[1] - start[1]) * mix),
        clamp_rgb(start[2] + (end[2] - start[2]) * mix),
    )

def shade_rgb(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Darken or brighten an RGB color while staying in the original hue family."""
    return (
        clamp_rgb(color[0] * factor),
        clamp_rgb(color[1] * factor),
        clamp_rgb(color[2] * factor),
    )

# Core Color Theme Palette (Gemini Inspired Minimalist Slate)
C_RESET = "\033[0m"
C_BOLD = "\033[1m"

C_PRIMARY = fg_rgb(241, 245, 249)     # Soft white (Slate 100)
C_MUTED = fg_rgb(148, 163, 184)       # Muted slate (Slate 400)
C_DARK = fg_rgb(71, 85, 105)          # Dark slate (Slate 600)
C_BORDER = fg_rgb(51, 65, 85)         # Very dark slate for thin dividers (Slate 700)

C_GEMINI_BLUE = fg_rgb(99, 102, 241)   # Indigo 400
C_GEMINI_PURPLE = fg_rgb(168, 85, 247) # Violet 400
C_GEMINI_PINK = fg_rgb(244, 63, 94)    # Rose 500
C_GEMINI_CYAN = fg_rgb(34, 211, 238)   # Cyan 400

C_GREEN = fg_rgb(52, 211, 153)        # Mint/Emerald 400
C_RED = fg_rgb(251, 113, 133)         # Rose/Pink 400
C_YELLOW = fg_rgb(251, 191, 36)       # Amber 400
C_BLUE = fg_rgb(56, 189, 248)         # Sky Blue 400

# Badges with solid background fill
C_BG_GREEN = bg_rgb(6, 78, 59) + fg_rgb(110, 231, 183)      # Dark Green BG, light green text
C_BG_RED = bg_rgb(153, 27, 27) + fg_rgb(254, 202, 202)      # Dark Red BG, light red text
C_BG_YELLOW = bg_rgb(120, 53, 4) + fg_rgb(253, 224, 71)     # Dark Amber BG, light yellow text
C_BG_BLUE = bg_rgb(30, 58, 138) + fg_rgb(147, 197, 253)     # Dark Blue BG, light blue text
C_BG_INDIGO = bg_rgb(49, 46, 129) + fg_rgb(199, 210, 254)   # Dark Indigo BG, light indigo text
C_BG_CYAN = bg_rgb(22, 78, 99) + fg_rgb(165, 243, 252)       # Dark Cyan BG, light cyan text
C_BG_DARK = bg_rgb(30, 41, 59) + fg_rgb(148, 163, 184)      # Dark Slate BG, slate-400 text
C_BG_ORANGE = bg_rgb(124, 45, 18) + fg_rgb(254, 215, 170)   # Dark Orange BG, light orange text

# Common standard aliases
RESET = C_RESET
BOLD = C_BOLD
GREEN = C_GREEN
RED = C_RED
YELLOW = C_YELLOW
BLUE = C_BLUE
CYAN = C_GEMINI_CYAN
MAGENTA = C_GEMINI_PURPLE
WHITE = C_PRIMARY

BG_GREEN = C_BG_GREEN
BG_RED = C_BG_RED
BG_YELLOW = C_BG_YELLOW
BG_BLUE = C_BG_BLUE

BB_AMBER = C_GEMINI_CYAN
BB_BG_AMBER = C_BG_INDIGO
BB_CYAN = C_GEMINI_BLUE

# Regex pattern for stripping ANSI sequences including 24-bit TrueColor
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])')

def make_gemini_gradient(text: str) -> str:
    """Applies a smooth Gemini color gradient (Indigo -> Purple -> Pink -> Cyan) character-by-character."""
    clean_text = ANSI_ESCAPE.sub('', text)
    non_space_chars = [c for c in clean_text if not c.isspace()]
    n = len(non_space_chars)
    if n == 0:
        return text
    colors = [
        (99, 102, 241),   # Indigo 400
        (168, 85, 247),  # Purple 400
        (244, 63, 94),   # Pink 500
        (34, 211, 238)   # Cyan 400
    ]
    result = []
    char_idx = 0
    for char in clean_text:
        if char.isspace():
            result.append(char)
            continue
        if n > 1:
            t = char_idx / (n - 1)
        else:
            t = 0.5
        
        num_segments = len(colors) - 1
        segment_len = 1.0 / num_segments
        segment_idx = int(t // segment_len)
        if segment_idx >= num_segments:
            segment_idx = num_segments - 1
            
        segment_t = (t - segment_idx * segment_len) / segment_len
        
        c1 = colors[segment_idx]
        c2 = colors[segment_idx + 1]
        
        r = int(c1[0] + segment_t * (c2[0] - c1[0]))
        g = int(c1[1] + segment_t * (c2[1] - c1[1]))
        b = int(c1[2] + segment_t * (c2[2] - c1[2]))
        
        result.append(f"\033[38;2;{r};{g};{b}m{char}")
        char_idx += 1
        
    return "".join(result) + C_RESET
