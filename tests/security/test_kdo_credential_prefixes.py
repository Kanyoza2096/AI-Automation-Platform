import pytest


def test_generated_credential_vault_key_has_kdo_prefix_and_round_trips():
    pytest.importorskip("cryptography.fernet")
    from core.credential_vault import CredentialVault

    key = CredentialVault.generate_key()
    assert key.startswith("KDO_ENCRYPTION_")

    vault = CredentialVault(encryption_key=key)
    assert vault.is_encrypted_mode
    encrypted = vault.encrypt("KDO credential test")
    assert encrypted.startswith("enc::")
    assert vault.decrypt(encrypted) == "KDO credential test"
