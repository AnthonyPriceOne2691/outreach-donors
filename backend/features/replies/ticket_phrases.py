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
        # Проверка прода 25.11.2026: «We’ve created a new ticket (#48213)», «thank you for
        # opening a new request» — служба заводит заявку сама.
        # Голое «request» — не заявка службы: «we have opened a request with our procurement
        # department» пишет человек (ревью продаж 25.11.2026), поэтому — с «new» или «support».
        r"\bwe(?:\s+have|['’]ve)\s+(?:created|opened|logged|registered)\s+a\s+"
        r"(?:new\s+(?:support\s+)?(?:ticket|case|request)|support\s+(?:ticket|case|request)|ticket|case)\b",
        r"\bthank(?:s|\s+you)\s+for\s+opening\s+a\s+new\s+(?:support\s+)?(?:ticket|case|request)\b",
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

#: Знак номера после слова заявки: «#», «№», «No.», «Nr.», «n°», «ID», «number», «номер».
#: «ID» — проверка прода 25.11.2026: «Your ticket ID is 48213-E» номером не считался.
_NUMBER_SIGN = r"(?:#|№|n[°º]|no\.?|nr\.?|id|number|nummer|número|numero|numer|номер)"
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

#: Лицо службы поддержки: «our support team», «a member of our Customer Service Team», «one of
#: our support agents». Само по себе ничего не решает: «the customer service team will be able to
#: help you, please write to them» пишет человек (ревью продаж 25.11.2026). Решает вместе с
#: обещанием ответить в срок (`ANSWER_LATER_RE`) — правило «служба обещает ответить» (`classify`).
SERVICE_TEAM: Table = {
    "en": (
        r"\b(?:our|the|one\s+of\s+our|a\s+member\s+of\s+(?:our|the))\s+(?:customer\s+)?"
        r"(?:support|service|care|help\s*desk|customer\s+service|customer\s+care)\s+"
        r"(?:team|agents?|specialists?|representatives?|staff)\b",
    ),
    "de": (
        r"\b(?:unser(?:e[mn]?)?|ein\s+mitarbeiter\s+unseres)\s+(?:support|kundenservice|kundendienst)"
        r"(?:-?team)?s?\b",
    ),
    "fr": (
        r"\b(?:notre|un\s+membre\s+de\s+notre)\s+(?:équipe\s+(?:support|du\s+support|du\s+service\s+client)"
        r"|service\s+client|support)\b",
    ),
    "es": (
        r"\b(?:nuestro|un\s+miembro\s+de\s+nuestro)\s+(?:equipo\s+de\s+(?:soporte|atención)"
        r"|servicio\s+de\s+atención(?:\s+al\s+cliente)?|soporte)\b",
    ),
    "it": (
        r"\b(?:il\s+nostro|un\s+membro\s+del\s+nostro)\s+(?:team\s+di\s+supporto|servizio\s+clienti|"
        r"supporto)\b",
    ),
    "pt": (
        r"\b(?:nossa\s+equipe\s+de\s+(?:suporte|atendimento)|nosso\s+(?:suporte|atendimento))\b",
    ),
    "nl": (
        r"\b(?:ons|een\s+medewerker\s+van\s+ons)\s+(?:supportteam|klantenservice|serviceteam)\b",
    ),
    "pl": (r"\b(?:nasz|naszego)\s+(?:zespół\s+(?:wsparcia|obsługi)|konsultant|dział\s+obsługi)\b",),
    "ru": (
        r"\b(?:наш[аи]?\s+)?(?:служба\s+поддержки|специалист[ыа]?\s+(?:службы\s+)?поддержки|"
        r"оператор[ыа]?\s+поддержки)\b",
    ),
}

#: Обещание ответить — глаголом ответа и сроком, в любом порядке внутри фразы: «will get back to
#: you as soon as possible», «responderemos lo antes posible», «sich bald bei Ihnen melden». Без
#: срока не обещание: «Служба поддержки ответит вам» — человек, передавший письмо.
PROMISE_VERB: Table = {
    "en": (
        r"get\s+back\s+to\s+you|respond(?:\s+to\s+you)?|reply(?:\s+to\s+you)?|be\s+in\s+touch|"
        r"contact\s+you|assist\s+you|answer\s+you",
    ),
    "de": (r"melden|antworten|kontaktieren|zurückmelden",),
    "fr": (
        r"répondr(?:a|ons|ont)|reviendr(?:a|ons|ont)\s+vers\s+vous|(?:re)?contacter(?:a|ons|ont)",
    ),
    "es": (r"responder(?:á|emos|án)|contactar(?:á|emos|án)|pondr(?:á|emos|án)\s+en\s+contacto",),
    "it": (r"rispond(?:erà|eremo|eranno)|(?:ri)?contatt(?:erà|eremo|eranno)",),
    "pt": (
        r"respond(?:erá|eremos|erão)|retorn(?:ará|aremos|arão)|entr(?:ará|aremos|arão)\s+em\s+contato",
    ),
    "nl": (r"contact|reageren|antwoorden",),
    "pl": (r"odpowie(?:my)?|skontaktuje(?:my)?\s+się|odezwie(?:my)?\s+się",),
    "ru": (r"ответ(?:им|ит|ят)|свяж(?:емся|ется|утся)",),
}
PROMISE_TIME: Table = {
    "en": (
        r"as\s+soon\s+as\s+(?:possible|we\s+can)|shortly|as\s+quickly\s+as\s+possible|within\s+\d+|"
        r"in\s+the\s+next\s+\d+",
    ),
    "de": (
        r"so\s+schnell\s+wie\s+möglich|schnellstmöglich|in\s+kürze|zeitnah|innerhalb\s+von\s+\d+|bald",
    ),
    "fr": (
        r"dans\s+les\s+(?:plus\s+)?brefs\s+délais|rapidement|sous\s+\d+|au\s+plus\s+vite|"
        r"dès\s+que\s+possible",
    ),
    "es": (r"lo\s+antes\s+posible|en\s+breve|a\s+la\s+mayor\s+brevedad|en\s+las\s+próximas\s+\d+",),
    "it": (r"al\s+più\s+presto|a\s+breve|il\s+prima\s+possibile|entro\s+\d+",),
    "pt": (
        r"o\s+mais\s+(?:breve|rápido)\s+possível|em\s+breve|assim\s+que\s+possível|em\s+até\s+\d+",
    ),
    "nl": (r"zo\s+snel\s+mogelijk|spoedig|binnenkort|binnen\s+\d+",),
    "pl": (r"najszybciej\s+jak|jak\s+najszybciej|wkrótce|niezwłocznie|w\s+ciągu\s+\d+",),
    "ru": (r"в\s+ближайшее\s+время|как\s+можно\s+скорее|в\s+кратчайшие\s+сроки|в\s+течение\s+\d+",),
}

#: Оценка разговора в чате поддержки — письмо из одной строки: «Rate your conversation»
#: (проверка прода 25.11.2026). Решает только в очень коротком письме (`classify`).
CHAT_RATING: Table = {
    "en": (r"\b(?:rate|review)\s+(?:your|this|the)\s+(?:conversation|chat)\b",),
    "de": (r"\bbewerten\s+sie\s+(?:dieses|das|ihr)\s+(?:gespräch|chat)\b",),
    "fr": (r"\b(?:évaluez|notez)\s+(?:cette|votre)\s+(?:conversation|discussion)\b",),
    "es": (r"\b(?:valora|califica|evalúa)\s+(?:esta|tu|su)\s+(?:conversación|chat)\b",),
    "it": (r"\b(?:valuta|vota)\s+(?:questa|la\s+tua)\s+(?:conversazione|chat)\b",),
    "pt": (r"\b(?:avalie|classifique)\s+(?:esta|sua|a)\s+(?:conversa|conversação)\b",),
    "nl": (r"\bbeoordeel\s+(?:dit|uw|je)\s+(?:gesprek|chat)\b",),
    "pl": (r"\boceń\s+(?:tę|swoją)\s+(?:rozmowę|konwersację)\b",),
    "ru": (r"\bоцените\s+(?:этот\s+|наш\s+)?(?:диалог|разговор|чат)\b",),
}


def _either_order(verb: str, time: str) -> str:
    return rf"\b(?:{verb})\b[^.!?]{{0,60}}?\b(?:{time})|\b(?:{time})\b[^.!?]{{0,60}}?\b(?:{verb})\b"


SERVICE_TEAM_RE = compiled(SERVICE_TEAM)
#: Обещание ответить в срок — глагол и срок одной фразой, на всех языках словаря.
ANSWER_LATER_RE = re.compile(
    "|".join(
        f"(?:{_either_order(verb, time)})"
        for lang in LANGUAGES
        for verb in PROMISE_VERB[lang]
        for time in PROMISE_TIME[lang]
    ),
    re.I,
)
CHAT_RATING_RE = compiled(CHAT_RATING)
