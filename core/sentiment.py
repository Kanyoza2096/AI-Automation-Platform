"""
Sentiment analysis for conversation intelligence.
"""

from typing import Tuple


# Sentiment indicators
NEGATIVE_WORDS = {
    'angry', 'furious', 'upset', 'annoyed', 'frustrated', 'hate', 'terrible',
    'awful', 'horrible', 'worst', 'stupid', 'useless', 'rubbish', 'bad',
    'disappointed', 'sad', 'unhappy', 'depressed', 'worried', 'stressed',
    'annoying', 'irritating', 'pathetic', 'waste', 'broken', 'failed',
}

POSITIVE_WORDS = {
    'love', 'awesome', 'great', 'excellent', 'amazing', 'fantastic', 'wow',
    'wonderful', 'brilliant', 'perfect', 'beautiful', 'happy', 'excited',
    'grateful', 'thankful', 'impressed', 'incredible', 'outstanding',
    'superb', 'blessed', 'glad', 'pleased', 'delighted', 'thrilled',
}

URGENT_WORDS = {
    'urgent', 'asap', 'emergency', 'immediately', 'critical', 'help',
    'please help', 'need help', 'important', 'priority', 'now',
}


def detect_sentiment(text: str) -> str:
    """
    Detect the emotional sentiment of a message.
    
    Returns:
        'positive', 'negative', 'neutral', 'urgent'
    """
    text_lower = text.lower()
    words = set(text_lower.split())
    
    # Check urgency first
    urgent_count = sum(1 for w in URGENT_WORDS if w in text_lower)
    if urgent_count >= 1:
        return 'urgent'
    
    pos_count = sum(1 for w in words if w in POSITIVE_WORDS)
    neg_count = sum(1 for w in words if w in NEGATIVE_WORDS)
    
    if neg_count > pos_count:
        return 'negative'
    elif pos_count > neg_count:
        return 'positive'
    return 'neutral'


def get_sentiment_response_instruction(sentiment: str) -> str:
    """Get response instructions based on sentiment."""
    instructions = {
        'negative': (
            "\nIMPORTANT: User seems frustrated or upset. "
            "Be extra empathetic and patient. "
            "Acknowledge their feelings. "
            "Offer specific, helpful solutions. "
            "Do NOT be dismissive or overly cheerful."
        ),
        'positive': (
            "\nUser is in a good mood. "
            "Match their positive energy. "
            "Feel free to be more playful and engaging."
        ),
        'urgent': (
            "\nUser seems to need urgent help. "
            "Be direct, clear, and solution-focused. "
            "Skip pleasantries. Get straight to the point. "
            "Provide actionable steps immediately."
        ),
        'neutral': "\nBe natural and conversational.",
    }
    return instructions.get(sentiment, instructions['neutral'])