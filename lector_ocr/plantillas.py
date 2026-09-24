r"""Las dos estructuras de carpetas a las que se archiva un documento.

Son datos, no logica: la lista de carpetas que tiene un expediente segun su materia.
Viven aqui y no repartidas por el codigo porque clasificacion.py las necesita para
comprobarse a si misma -- que todo destino que declara exista de verdad -- y un
destino mal escrito no falla haciendo ruido: crearia una carpeta nueva, y eso no se
nota hasta que alguien no encuentra sus papeles.

Para adaptarlo a otra organizacion se sustituyen estos dos diccionarios y ya: el
resto del paquete los consume sin saber que hay dentro.
"""

from __future__ import annotations

PLANTILLA_FORECLOSURE: dict[str, dict] = {
    "01_General": {},
    "02_Loss Mitigation": {
        "Bank Statements": {}, "Mortgage Statements": {}, "Pay Stubs": {},
        "Taxes": {}, "Utility Bills": {},
    },
    "03_Litigation": {
        "Discovery": {}, "Motions": {}, "Orders": {}, "Pleadings": {},
    },
    "04_Correspondence": {},
}

PLANTILLA_FCRA: dict[str, dict] = {
    "01_Documents from client": {},
    "02_FCRA Dispute": {
        "Credit Report": {}, "Dispute Letters": {}, "Identifying Documents": {},
    },
    "03_Complaint": {"Amended Complaint": {}},
    "04_Service of Process": {},
    "05_Answers": {},
    "06_Case Management Plan Initial Conference": {
        "Rule 26(a)(1)": {"Defendant Rule 26(a)(1)": {}},
    },
    "07_Discovery": {
        "Defendant Expert Witness Report": {}, "Defendants Demands": {},
        "Deficiency Letters": {}, "Plaintiff Demands": {},
        "Plaintiff Expert Witness Report": {},
        "Responses to Defendants Demands": {}, "Responses to Plaintiff Demands": {},
    },
    "08_Depositions": {"Notices of Deposition": {}},
    "09_Motions": {
        "Adjournment Requests": {}, "Pro Hac Vice Motions": {},
        "Rule 12": {
            "Defendants Rule 12 Motion": {},
            "Defendants Rule 12 PMC Letter": {},
            "Plaintiff Response to 12 Motion": {},
            "Plaintiff Response to Rule 12 PMC Letter": {},
        },
    },
    "10_Stipulations": {"Extension of Time": {}, "Voluntary Dismissal": {}},
    "11_Notices": {
        "Notice of Appeal": {}, "Notice of Appearance": {},
        "Notice of Dismissal": {}, "Notice of Settlement": {},
    },
    "12_Settlement Documents": {
        "Ex-Parte Settlement Letter": {}, "Itemized Damages to Defendants": {},
    },
    "13_Status Reports": {},
    "14_Billing and Invoices": {},
}


