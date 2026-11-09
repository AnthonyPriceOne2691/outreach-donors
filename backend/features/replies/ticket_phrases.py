"""Подтверждение заявки системой поддержки — фразы и номер заявки на языках рынков.

Отдельно от `phrases`: там словари слов, которые пишет автомат о себе (отпуск, отписка,
отказ доставки), а здесь — то, чем служба поддержки отвечает на любое письмо: «ваше
обращение получено» и номер заявки. Правило (`classify`) требует их вместе: фразу о
приёме пишет и человек, передавший письмо коллеге, а в заявку с номером отвечает и
живой сотрудник. Языки и сборка шаблона — общие с `phrases`.
"""

from __future__ import annotations

import re

from backend.features.replies.phrases import LANGUAGES, compiled

Table = dict[str, tuple[str, ...]]

#: Подтверждение заявки системой поддержки: «ваше обращение получено»,
#: «your case has been assigned», «оцените качество обслуживания». Так пишет
#: и живой сотрудник поддержки, поэтому фраза решает только вместе с номером
#: заявки (`TICKET_NUMBER_RE`), а сумма и файл её перевешивают (`classify`).
#: Проверка прода 06–09.11.2026: три подтверждения служб поддержки с `support@`
#: («#… Заявка закрыта», «[#…] Re: …», «Case Creation Confirmation - Case #…»)
#: стояли ответом человека и ждали его на экране.
#:
#: «Ваше письмо получили» без лица службы сюда не входит: так отвечает и
#: человек, который передал письмо коллеге.
TICKET_RECEIPT: Table = {
    "en": (
        r"\byour\s+(?:support\s+)?(?:request|ticket|case|enquiry|inquiry|message|e-?mail|query)\b"
        r"[^.!?]{0,60}?\b(?:has|have|was|were|is)\s+(?:been\s+|now\s+)?"
        r"(?:received|created|logged|registered|opened|forwarded|assigned|escalated|submitted)\b",
        r"\bwe(?:\s+have|['’]ve)\s+(?:successfully\s+)?received\s+your\s+(?:support\s+)?"
        r"(?:request|ticket|case|enquiry|inquiry|message|e-?mail|query)\b",
        r"\byour\s+(?:case|ticket|request|reference)\s+(?:number|no\.?|id|#)\s+is\b",
        r"\b(?:please\s+)?rate\s+(?:our|the)\s+(?:support|service|customer\s+service|response|agent)\b",
    ),
    "de": (
        r"\bihre?\s+(?:anfrage|nachricht|e-?mail|ticket|meldung|support-?anfrage)\b[^.!?]{0,60}?"
        r"\b(?:ist|wurde|wird)\b[^.!?]{0,30}?"
        r"\b(?:eingegangen|erhalten|registriert|angelegt|erfasst|weitergeleitet|zugewiesen)\b",
        r"\bwir\s+haben\s+ihre?\s+(?:anfrage|nachricht|e-?mail)\s+(?:erhalten|bekommen)\b",
        r"\b(?:ticket|vorgangs|anfrage)-?\s*(?:nummer|nr\.)",
        r"\bbewerten\s+sie\s+(?:unseren?\s+|den\s+)?(?:service|support|kundenservice)\b",
    ),
    "fr": (
        r"\bvotre\s+(?:demande|message|ticket|requête|e-?mail|dossier)\b[^.!?]{0,60}?"
        r"\b(?:a\s+(?:bien\s+)?été|est)\s+(?:bien\s+)?"
        r"(?:reçue?|enregistrée?|créée?|transmise?|prise?\s+en\s+compte|attribuée?)",
        r"\bnous\s+avons\s+(?:bien\s+)?reçu\s+votre\s+(?:demande|message|e-?mail|requête)\b",
        r"\bnuméro\s+de\s+(?:ticket|dossier|demande)\b",
        r"\b(?:évaluez|notez)\s+(?:notre|le)\s+(?:service|support)\b",
    ),
    "es": (
        r"\bsu\s+(?:solicitud|mensaje|consulta|ticket|caso|petición|correo)\b[^.!?]{0,60}?"
        r"\b(?:ha\s+sido|fue)\s+(?:recibid[oa]|registrad[oa]|cread[oa]|asignad[oa]|remitid[oa])\b",
        r"\bhemos\s+recibido\s+su\s+(?:solicitud|mensaje|consulta|correo|petición)\b",
        r"\bnúmero\s+de\s+(?:ticket|caso|solicitud|incidencia)\b",
        r"\bvalore\s+(?:nuestr[oa]\s+|el\s+|la\s+)?(?:servicio|soporte|atención)\b",
    ),
    "it": (
        r"\bla\s+(?:sua|tua|vostra)\s+(?:richiesta|segnalazione|e-?mail|pratica)\b[^.!?]{0,60}?"
        r"(?:\bè|\be['’])\s+stata\s+(?:ricevuta|registrata|presa\s+in\s+carico|inoltrata|assegnata)\b",
        r"\babbiamo\s+ricevuto\s+(?:la\s+|il\s+)?(?:sua|tua|vostra|suo|tuo|vostro)\s+"
        r"(?:richiesta|e-?mail|segnalazione|messaggio)\b",
        r"\bnumero\s+(?:del\s+|della\s+)?(?:ticket|pratica|richiesta)\b",
        r"\bvaluta\s+il\s+(?:nostro\s+)?(?:servizio|supporto)\b",
    ),
    "pt": (
        r"\b(?:sua|seu)\s+(?:solicitação|solicitacao|pedido|chamado|mensagem|ticket|e-?mail)\b"
        r"[^.!?]{0,60}?\b(?:foi|está)\s+(?:recebid[oa]|registrad[oa]|criad[oa]|encaminhad[oa]|atribuíd[oa])\b",
        r"\brecebemos\s+(?:a\s+sua|o\s+seu|sua|seu)\s+(?:solicitação|pedido|mensagem|e-?mail|chamado)\b",
        r"\bnúmero\s+do\s+(?:chamado|ticket|protocolo|pedido)\b",
        r"\bavalie\s+(?:o\s+)?(?:nosso\s+)?(?:atendimento|suporte|serviço)\b",
    ),
    "nl": (
        r"\buw\s+(?:verzoek|aanvraag|bericht|e-?mail|ticket|melding|vraag)\b[^.!?]{0,60}?"
        r"\b(?:is|werd)\s+(?:in\s+goede\s+orde\s+)?"
        r"(?:ontvangen|geregistreerd|aangemaakt|doorgestuurd|toegewezen)\b",
        r"\b(?:wij|we)\s+hebben\s+uw\s+(?:verzoek|aanvraag|bericht|e-?mail|vraag)\s+"
        r"(?:in\s+goede\s+orde\s+)?ontvangen\b",
        r"\b(?:ticket|melding)snummer\b|\bticketnummer\b",
        r"\bbeoordeel\s+(?:onze|de)\s+(?:service|klantenservice|dienstverlening)\b",
    ),
    "pl": (
        r"\b(?:twoje|pana|pani|państwa)\s+zgłoszenie\b[^.!?]{0,60}?"
        r"\bzostało\s+(?:przyjęte|zarejestrowane|utworzone|przekazane|przydzielone)\b",
        r"\botrzymaliśmy\s+(?:twoje|pana|pani|państwa)\s+(?:zgłoszenie|wiadomość|zapytanie)\b",
        r"\bnumer\s+zgłoszenia\b",
        r"\boceń\s+(?:naszą\s+)?(?:obsługę|pomoc)\b",
    ),
    "ru": (
        r"\bваш[еаи]?\s+(?:обращение|заявка|запрос|письмо|сообщение|вопрос)\b[^.!?]{0,60}?"
        r"\b(?:был[аои]?\s+)?(?:получен|принят|зарегистрирован|передан|направлен|создан)[аоы]?\b",
        r"\bприсвоен[оа]?\s+(?:номер|№)",
        r"\bномер\s+(?:вашего\s+|вашей\s+)?(?:обращения|заявки|запроса|тикета)\b",
        r"\bоцените\s+(?:качество|нашу\s+работу|работу|ответ)\b",
        r"\bмы\s+(?:уже\s+)?занимаемся\s+вашим\s+(?:вопросом|обращением|запросом)\b",
    ),
}

#: Как система поддержки называет заявку перед её номером: «Case #48213»,
#: «Заявка № 1234», «Ticket-Nr. 77812». Само слово заявкой письмо не делает —
#: только с номером сразу за ним (`TICKET_NUMBER_RE`).
TICKET_WORD: Table = {
    "en": (r"ticket", r"case", r"request", r"incident"),
    "de": (r"ticket", r"anfrage", r"vorgang"),
    "fr": (r"ticket", r"demande", r"dossier"),
    "es": (r"ticket", r"caso", r"solicitud", r"incidencia"),
    "it": (r"ticket", r"richiesta", r"pratica"),
    "pt": (r"ticket", r"chamado", r"protocolo"),
    "nl": (r"ticket", r"melding", r"verzoek"),
    "pl": (r"zgłoszeni[ea]", r"sprawa"),
    "ru": (r"заявк[аиу]", r"обращени[еяю]", r"тикет[ау]?", r"запрос[ау]?"),
}

TICKET_RECEIPT_RE = compiled(TICKET_RECEIPT)

#: Знак номера после слова заявки: «#», «№», «No.», «Nr.», «n°», «number», «номер».
_NUMBER_SIGN = r"(?:#|№|n[°º]|no\.?|nr\.?|number|nummer|número|numero|numer|номер)"
_TICKET_WORDS = "|".join(word for lang in LANGUAGES for word in TICKET_WORD[lang])

#: Номер заявки — в теме или в тексте. Голое число номером не считается:
#: цена, год и телефон пишутся так же. Номер — со значком или с именем заявки
#: перед ним: «[#48213]», «(CC-500017)», «#300000417», «№ 12345», «Case #48213»,
#: «case number is 48213», «под номером 4512». Решётка после «&», «/» и буквы —
#: не номер: так пишут HTML-сущности и якоря ссылок. Год после слова — не номер:
#: «in case 2025» пишет человек.
TICKET_NUMBER_RE = re.compile(
    r"\[\s*(?:#\s*|[a-z]{1,6}-)\d{3,}\s*\]"
    r"|\(\s*[a-z]{1,6}-\d{4,}\s*\)"
    r"|(?<![\w#&/])#\s?\d{4,}\b"
    r"|№\s?\d{3,}"
    r"|\bпод\s+номером\s+\d{3,}"
    rf"|\b(?:{_TICKET_WORDS})\s*{_NUMBER_SIGN}?\s*(?:is\s+)?[:#]?\s*(?:[a-z]{{1,6}}-)?"
    r"(?!(?:19|20)\d\d\b)\d{3,}\b",
    re.I,
)
