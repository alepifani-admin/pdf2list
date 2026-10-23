from pdf2list.core.extract import _collect_configs
from pdf2list.core.models import PageText, Word
from pdf2list.core.profiles import load_profile


def test_collect_configs_from_badge():
    words = [
        Word("SISTEMI", 40, 40, 100, 50),
        Word("Codice", 40, 200, 80, 210),
        Word("Sistema", 82, 200, 130, 210),
        Word("visualizzato:", 132, 200, 210, 210),
        Word("2xCOB90NR01", 212, 200, 300, 210),
        Word("|", 302, 200, 306, 210),
        Word("Prezzo:", 308, 200, 350, 210),
        Word("€", 352, 200, 360, 210),
        Word("819,00", 362, 200, 410, 210),
    ]
    page = PageText(number=3, width=600, height=800, words=words)
    configs = _collect_configs(page, [], load_profile("fernova"), 3)
    assert len(configs) == 1
    assert configs[0]["codice"] == "2xCOB90NR01"
    assert configs[0]["prezzo"] == "819,00"
    assert configs[0]["sezione"] == "SISTEMI"
