import re
from typing import Any, Dict, List, Union

class PIIRedactor:
    """Utility class to detect and redact Personally Identifiable Information (PII).
    Handles scrubbing of strings and recursive cleaning of nested dictionaries/lists.
    """
    
    # Regex patterns for standard PII types
    EMAIL_PATTERN = re.compile(
        r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    )
    
    # Matches common international and US phone number formats
    PHONE_PATTERN = re.compile(
        r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
    )
    
    # Matches 13 to 19 digit credit card patterns (Luhn validation is not performed, pure regex match)
    CREDIT_CARD_PATTERN = re.compile(
        r'\b(?:\d[ -]*?){13,19}\b'
    )
    
    # Matches US Social Security Numbers or standard national IDs (XXX-XX-XXXX)
    SSN_PATTERN = re.compile(
        r'\b\d{3}-\d{2}-\d{4}\b'
    )

    def redact_text(self, text: str) -> str:
        """Applies regex replacements to a string to scrub PII.
        """
        if not isinstance(text, str):
            return text
            
        # Scrub Credit Cards first
        text = self.CREDIT_CARD_PATTERN.sub("[CREDIT_CARD_REDACTED]", text)
        
        # Scrub SSNs
        text = self.SSN_PATTERN.sub("[SSN_REDACTED]", text)
        
        # Scrub Emails
        text = self.EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)
        
        # Scrub Phone Numbers
        text = self.PHONE_PATTERN.sub("[PHONE_REDACTED]", text)
        
        return text

    def redact_data(self, data: Any) -> Any:
        """Recursively traverses nested dictionaries, lists, or primitive types
        and redacts PII elements.
        """
        if isinstance(data, dict):
            cleaned_dict = {}
            for key, val in data.items():
                key_lower = key.lower()
                if key_lower in ["name", "first_name", "last_name", "fullname", "full_name"]:
                    cleaned_dict[key] = "[NAME_REDACTED]"
                elif key_lower in ["password", "secret", "cvv", "pin"]:
                    cleaned_dict[key] = "[SECRET_REDACTED]"
                else:
                    cleaned_dict[key] = self.redact_data(val)
            return cleaned_dict
            
        elif isinstance(data, list):
            return [self.redact_data(item) for item in data]
            
        elif isinstance(data, str):
            return self.redact_text(data)
            
        else:
            return data

# Global redactor instance
pii_redactor = PIIRedactor()
