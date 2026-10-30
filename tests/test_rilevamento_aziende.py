# -*- coding: utf-8 -*-
"""
Test per il modulo moduli/rilevamento_aziende.py.

Verifica:
- Eliminazione dei falsi positivi causati dalla preposizione 'DI' (cognomi patronimici, note)
- Rilevamento corretto delle vere ditte individuali con 'DI'
- Rilevamento di società ordinarie, condomini, enti pubblici e contatori semidiretti
- Funzione di classificazione e arricchimento contatti
"""
import pytest
from moduli.rilevamento_aziende import (
    is_azienda,
    is_semidiretto,
    classifica_tipo_contatto,
    arricchisci_contatti,
)


class TestFalsiPositiviPreposizioneDi:
    """Verifica che persone fisiche con cognome 'DI' non siano rilevate come aziende."""

    @pytest.mark.parametrize("nominativo", [
        "DANIELE DI GIUSEPPE",
        "GIUSEPPINA DI FRANCO",
        "CARMELINA DI BELLA",
        "ELEONORA DI STEFANO",
        "ALDO DI GIOIA",
        "ALDO DI GIOIA 347 454 6900",
        "MICHELE DI STEFANO",
        "DALILA DI SALVO",
        "PIETRO DI LUCCIO",
        "MICHELE DI MATTEO",
        "MAURO DI CARLO",
        "ROSALBA DI ROSA",
        "ROSI DI MATTEO",
        "PIETRO DI IORIO",
        "MARIA DI FRONZO",
        "AUGUSTO DI BARTOLOMEO",
        "LUCIA DI MARTINO",
        "SANTO DI MASI",
        "MARIANO DI ROSA",
        "VALERIA DI LASCIA",
        "NAZARIO DI SUMMA",
        "CIRO DI PIERNO",
        "FILOMENA DI GRAZIA",
        "LAURA DI NUNNO",
        "GIULIA DI SILVESTRO 3896891268",
        "DANIELE DI FIORE",
        "FABIO DI NARZO",
        "MAURIZIO DI SANO 333 260 8632",
        "DI TIZIO MONICA",
        "DI BARTOLO ROBERTO ANTONINO",
        "DI DAGRADI ALESSANDRO STEFANO",
        "DI MAIO LUIGI",
        "MARIA RITA DI GIORGI",
        "MARIA CARMINA DI NUZZO",
        "AMELIA SAMANTHA DI NOBILE",
        "MARIA ROSARIA DI PALMA",
        "MARIA PAOLA LUDA DI CORTEMIGLIA",
        "RITA MARIA BATT DI MURO",
        "LUIGIA CHIESA",
        "CHIESA FEDERICO",
    ])
    def test_privati_con_di_non_sono_aziende(self, nominativo):
        assert is_azienda(nominativo) is False
        assert classifica_tipo_contatto(nominativo) == "privato"

    @pytest.mark.parametrize("nominativo", [
        "VINCENZA TUCCI cognata di benedetto",
        "GUIDO TENDERINI 346 713 2702 moglie di GIULIO BERTOLDINI",
        "MARCO ROSSI per conto di ANNA BIANCHI",
        "LUIGI VERDI erede di CARLO VERDI",
    ])
    def test_note_parentela_con_di_non_sono_aziende(self, nominativo):
        assert is_azienda(nominativo) is False
        assert classifica_tipo_contatto(nominativo) == "privato"


class TestNoteOperativeNelleColonne:
    """Verifica l'interazione con le colonne Note operative."""

    def test_note_parentela_escludono_azienda(self):
        # Nominativo ambiguo o con 'DI' dove la nota specifica che è la moglie/marito
        assert is_azienda("DANIELE DI GIUSEPPE", note="parlato con moglie per appuntamento") is False
        assert is_azienda("ALDO DI GIOIA", note="assente, richiamare il marito") is False
        assert is_azienda("ANTONIA BULFARO", note="chiesto al negozio accanto per il portone") is False

    def test_note_commerciali_rilevano_azienda(self):
        # Nominativo che sembra persona fisica ma la nota operativa indica sede/studio
        assert is_azienda("MARIO ROSSI", note="citofonare studio legale 1 piano") is True
        assert classifica_tipo_contatto("MARIO ROSSI", note="citofonare studio legale 1 piano") == "azienda"
        assert is_azienda("GIUSEPPE VERDI", note="sede aziendale alfa srl") is True
        assert is_azienda("ANNA BIANCHI", note="attività commerciale parrucchiera") is True
        assert is_azienda("PAOLO NERI", note="studio medico dott. paolo neri") is True


class TestVereDitteIndividualiConDi:
    """Verifica che le ditte individuali o insegne con 'DI' continuino ad essere rilevate come aziende."""

    @pytest.mark.parametrize("nominativo", [
        "LML LAVORAZIONI MECCANICHE LOCASTRO DI LOCASTRO MASSIMILIANO",
        "STAZIONE SERVIZIO ENI DI PRONINO DIEGO",
        "OLEODINAMICA BUSSO DI BUSSO ALESSANDRO 3387675050",
        "DEUX SOLEIL DI GIRGENTI VINCI FRANCO blackjosh1313@gmail.com",
        "PRODENTAL DI PELLIZZER MAURIZIO 347 970 7207",
        "CENTRO MEDICO D'AZEGLIO DI SORBO ENRICO",
        "EURODOLCE DI CASETTA PAOLO E SIMONE",
        "LA TANA DEI MONELLI DI MONTANTE MARCO",
        "PIANETA VACANZE DI VITO PEZZOLLA",
        "G-UNIT DI GIORGIA GERBINO",
        "TIPOGRAFIA MASSIMO DI PAUTASSO SIMONA 011 901 6230",
        "BARBER SHOP DI EL ARFAOUI WALID",
        "Cartobox DI Lottaroli Francesco",
        "BOCCARDO G. DI BOCCARDO PIERLUIGI",
        "IL FORNAIO DI ORRU' CHIARA",
        "BREAK CAFE' DI DIEGO MIRIMIN",
        "REZZANI LUIGI DI BARBARA REZZANI",
        "Z.B.S. SNC DI BORDONE B & C",
        "FONTANA DI FONTANA ANDREA E C. S.N.C",
        "D.I. ROSSI MARIO",
        "DITTA INDIVIDUALE BIANCHI CARLO",
    ])
    def test_ditte_con_di_sono_aziende(self, nominativo):
        assert is_azienda(nominativo) is True
        assert classifica_tipo_contatto(nominativo) == "azienda"


class TestEntiPubbliciECondomini:
    """Verifica la corretta classificazione di enti territoriali, istituzioni e condomini."""

    @pytest.mark.parametrize("nominativo", [
        "COMUNE DI TROFARELLO",
        "COMUNE CITTA' DI AVIGLIANA",
        "CITTA' METROPOLITANA DI TORINO",
        "PROVINCIA DI PAVIA",
        "UNIVERSITA' DEGLI STUDI DI PAVIA",
        "CASA DI RIPOSO UMBERTO I",
        "CROCE ROSSA ITALIANA COMITATO DI NICHELINO",
        "OSPEDALE MAGGIORE",
        "PARROCCHIA SAN ROCCO",
        "GUARDIA DI FINANZA",
    ])
    def test_enti_pubblici(self, nominativo):
        assert is_azienda(nominativo) is True
        assert classifica_tipo_contatto(nominativo) == "ente_pubblico"

    @pytest.mark.parametrize("nominativo", [
        "CONDOMINIO CORSO FRANCIA 10",
        "COND. VIA ROMA 25",
        "CONDOMINIO I CIPRESSI",
    ])
    def test_condomini(self, nominativo):
        assert is_azienda(nominativo) is True
        assert classifica_tipo_contatto(nominativo) == "condominio"


class TestSocietaOrdinarieESemidiretti:
    """Verifica società con suffissi commerciali classici e misuratori semidiretti."""

    @pytest.mark.parametrize("nominativo", [
        "ACME SRL",
        "BETA S.R.L.S.",
        "GAMMA SPA",
        "DELTA S.A.S.",
        "EPSILON SNC",
        "COOPERATIVA AURORA",
        "FARMACIA CENTRALE",
        "BAR DELLO SPORT",
        "AZIENDA AGRICOLA F.LLI ROSSI",
        "AZ. AGR. IL POGGIO",
    ])
    def test_societa_classiche(self, nominativo):
        assert is_azienda(nominativo) is True
        assert classifica_tipo_contatto(nominativo) == "azienda"

    def test_semidiretto_override(self):
        # Un nominativo che sembra privato ma è Semidiretto deve risultare azienda
        assert is_semidiretto("SEMIDIRETTO") is True
        assert is_semidiretto("TRIFASE") is False
        assert is_azienda("MARIO BIANCHI", tipologia_contatore="SEMIDIRETTO") is True
        assert classifica_tipo_contatto("MARIO BIANCHI", tipologia_contatore="SEMIDIRETTO") == "azienda"


class TestArricchimentoContatti:
    """Verifica arricchisci_contatti con diversi tipi di contatto."""

    def test_arricchisci_contatti(self):
        contatti = [
            {"nome": "DANIELE DI GIUSEPPE", "tipologia_contatore": "MONOFASE"},
            {"nome": "ACME SRL", "tipologia_contatore": "TRIFASE"},
            {"nome": "COMUNE DI PAVIA", "tipologia_contatore": ""},
            {"nome": "CONDOMINIO VIA PO 1", "tipologia_contatore": ""},
            {"nome": "MARIO BIANCHI", "tipologia_contatore": "SEMIDIRETTO"},
        ]
        arricchiti = arricchisci_contatti(contatti)
        assert arricchiti[0]["tipo_contatto"] == "privato"
        assert arricchiti[0]["is_azienda"] is False

        assert arricchiti[1]["tipo_contatto"] == "azienda"
        assert arricchiti[1]["is_azienda"] is True

        assert arricchiti[2]["tipo_contatto"] == "ente_pubblico"
        assert arricchiti[2]["is_azienda"] is True

        assert arricchiti[3]["tipo_contatto"] == "condominio"
        assert arricchiti[3]["is_azienda"] is True

        assert arricchiti[4]["tipo_contatto"] == "azienda"
        assert arricchiti[4]["is_azienda"] is True

