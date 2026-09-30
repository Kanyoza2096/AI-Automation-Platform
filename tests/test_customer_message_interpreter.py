from services.customer_message_interpreter import interpret_customer_message


def test_purchase_interest():
    assert interpret_customer_message("Yes, I want to buy this")['intent'] == 'purchase_interest'


def test_price_request():
    assert interpret_customer_message("Please send me the price")['intent'] == 'price_request'


def test_meeting_request():
    assert interpret_customer_message("Can you book a demo for me?")['intent'] == 'meeting_request'


def test_opt_out_is_authoritative():
    assert interpret_customer_message("I was interested but stop contacting me")['intent'] == 'opt_out'
