"""
Language detection for English, Chichewa, and mixed language.
"""

import re
from typing import Tuple


# Chichewa markers (common words)
CHICHEWA_MARKERS = {
    'ndi', 'kuti', 'zinthu', 'bwino', 'zikomo', 'muli', 'bwanji',
    'chifukwa', 'pamene', 'chonde', 'ndithu', 'ayi', 'inde',
    'moni', 'mwadzuka', 'mwachemoka', 'mwaswera', 'usiku', 'mawa',
    'lero', 'dzulo', 'chaka', 'munthu', 'anthu', 'mwana', 'ana',
    'nyumba', 'galimoto', 'njala', 'madzi', 'moto', 'dziko',
    'chuma', 'mphamvu', 'chikondi', 'mtendere', 'chimwemwe',
    'nthawi', 'ntchito', 'sukulu', 'chipatala', 'msika',
    'ndalama', 'foni', 'kompyuta', 'intaneti', 'mau',
    'chonde', 'pepani', 'ndikhululukireni', 'tithokoze',
    'ndapita', 'ndabwera', 'ndikupita', 'ndikubwera',
    'mulibwanji', 'ndiribwino', 'sindiri', 'sindikudziwa',
}

# English function words (high frequency)
ENGLISH_MARKERS = {
    'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have',
    'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do',
    'at', 'this', 'but', 'his', 'by', 'from', 'they', 'we',
    'her', 'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would',
}


def detect_language(text: str) -> str:
    """
    Detect the primary language of a message.
    
    Returns:
        'chichewa' - Primarily Chichewa
        'english' - Primarily English
        'mixed' - Significant mix of both
    """
    if not text or not text.strip():
        return 'english'
    
    text_lower = text.lower()
    words = set(text_lower.split())
    
    # Count markers
    chichewa_count = sum(1 for w in words if w in CHICHEWA_MARKERS)
    english_count = sum(1 for w in words if w in ENGLISH_MARKERS)
    
    # Total meaningful words
    total = chichewa_count + english_count
    if total == 0:
        # Check for Chichewa-like patterns
        chichewa_patterns = len(re.findall(r'[aeiou]{2,}', text_lower))  # Vowel sequences
        return 'chichewa' if chichewa_patterns > 3 else 'english'
    
    chichewa_ratio = chichewa_count / total
    
    if chichewa_ratio > 0.7:
        return 'chichewa'
    elif chichewa_ratio < 0.3:
        return 'english'
    else:
        return 'mixed'


def get_language_instruction(language: str) -> str:
    """Get the language instruction for the AI prompt."""
    instructions = {
        'chichewa': (
            "\n\nCRITICAL: Respond in natural, conversational Chichewa. "
            "Mix in English technical terms only when there's no good Chichewa equivalent. "
            "Use common Chichewa expressions naturally. "
            "Match the user's level of formality."
        ),
        'english': (
            "\n\nRespond in natural, conversational English. "
            "Be warm and friendly. Match the user's communication style."
        ),
        'mixed': (
            "\n\nRespond naturally. You can mix Chichewa and English "
            "the way Malawians naturally do in conversation. "
            "Use whichever language feels most natural for each part of your response."
        ),
    }
    return instructions.get(language, instructions['english'])