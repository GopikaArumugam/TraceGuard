from app.redactor import PIIRedactor

def test_redact_text_email():
    redactor = PIIRedactor()
    text = "Send confirmation to alice.smith@gmail.com immediately."
    redacted = redactor.redact_text(text)
    assert "[EMAIL_REDACTED]" in redacted
    assert "alice.smith" not in redacted

def test_redact_text_phone():
    redactor = PIIRedactor()
    text = "My contact number is 555-123-4567."
    redacted = redactor.redact_text(text)
    assert "[PHONE_REDACTED]" in redacted
    assert "555-123" not in redacted

def test_redact_text_credit_card():
    redactor = PIIRedactor()
    text = "Bill to card 1234-5678-1234-5678."
    redacted = redactor.redact_text(text)
    assert "[CREDIT_CARD_REDACTED]" in redacted

def test_redact_nested_dict():
    redactor = PIIRedactor()
    data = {
        "user_id": "usr_99",
        "name": "Bob Dylan",
        "contact": {
            "email": "bob@dylan.org",
            "phone": "202-555-0143"
        },
        "items": ["card 4111222233334444", "clean text"]
    }
    redacted = redactor.redact_data(data)
    
    assert redacted["name"] == "[NAME_REDACTED]"
    assert redacted["contact"]["email"] == "[EMAIL_REDACTED]"
    assert redacted["contact"]["phone"] == "[PHONE_REDACTED]"
    assert "[CREDIT_CARD_REDACTED]" in redacted["items"][0]
    assert redacted["items"][1] == "clean text"
