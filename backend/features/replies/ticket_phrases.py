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
        r"\bwe(?:\s+have|['’]ve)\s+(?:created|opened|logged|registered)\s+a\s+(?:new\s+)?"
        r"(?:support\s+)?(?:ticket|case|request)\b",
        r"\bthank(?:s|\s+you)\s+for\s+opening\s+a\s+(?:new\s+)?(?:support\s+)?(?:ticket|case|request)\b",
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

#: Служба обещает ответить позже — лицом службы: «our support team will respond shortly»,
#: «a member of our Customer Service Team will…», «one of our support agents will assist you».
#: Проверка прода 25.11.2026: среди ответов рекламодателей, ждавших человека, — такие
#: подтверждения без номера заявки. Человек о себе так не пишет: он говорит «я», «мы» или
#: «коллега», а не «команда поддержки». Решает только в коротком письме (`classify`): в
#: длинном деловом ответе «наша служба поддержки поможет с интеграцией» — часть дела.
SERVICE_PROMISE: Table = {
    "en": (
        r"\b(?:our|the|one\s+of\s+our|a\s+member\s+of\s+(?:our|the))\s+(?:customer\s+)?"
        r"(?:support|service|care|help\s*desk|customer\s+service|customer\s+care)\s+"
        r"(?:team|agents?|specialists?|representatives?|staff)\s+(?:will|shall|is\s+going\s+to)\b",
    ),
    "de": (
        r"\b(?:unser|ein\s+mitarbeiter\s+unseres)\s+(?:support|kundenservice|kundendienst|service)"
        r"(?:-?team)?s?\s+(?:wird|meldet\s+sich)\b",
    ),
    "fr": (
        r"\b(?:notre|un\s+membre\s+de\s+notre)\s+(?:équipe\s+(?:support|du\s+support|du\s+service\s+client)"
        r"|service\s+client|support)\s+(?:vous\s+)?(?:répondra|reviendra|contactera|recontactera)\b",
    ),
    "es": (
        r"\b(?:nuestro|un\s+miembro\s+de\s+nuestro)\s+(?:equipo\s+de\s+(?:soporte|atención)"
        r"|servicio\s+de\s+atención(?:\s+al\s+cliente)?|soporte)\s+(?:se\s+pondrá\s+en\s+contacto|"
        r"(?:le|te)\s+(?:responderá|contactará))\b",
    ),
    "it": (
        r"\b(?:il\s+nostro|un\s+membro\s+del\s+nostro)\s+(?:team\s+di\s+supporto|servizio\s+clienti|"
        r"supporto)\s+(?:ti|la|vi)\s+(?:risponderà|contatterà|ricontatterà)\b",
    ),
    "pt": (
        r"\b(?:nossa\s+equipe|nosso\s+suporte|nosso\s+atendimento)(?:\s+de\s+(?:suporte|atendimento))?\s+"
        r"(?:entrará\s+em\s+contato|responderá|retornará)\b",
    ),
    "nl": (
        r"\b(?:ons|een\s+medewerker\s+van\s+ons)\s+(?:supportteam|klantenservice|serviceteam|team)\s+"
        r"(?:neemt|zal|reageert)\b",
    ),
    "pl": (
        r"\bnasz\s+(?:zespół\s+(?:wsparcia|obsługi)|konsultant|dział\s+obsługi)\s+"
        r"(?:odpowie|skontaktuje\s+się)\b",
    ),
    "ru": (
        r"\b(?:наш[аи]?\s+)?(?:служба\s+поддержки|специалист[ыа]?\s+(?:службы\s+)?поддержки|"
        r"оператор[ыа]?\s+поддержки)\s+(?:свяж(?:е|у)тся|ответ(?:и|я)т)\b",
    ),
}

#: «Ответим вам как можно скорее» без лица службы — так пишет и человек, поэтому решает
#: только вместе с фразой о приёме обращения (`TICKET_RECEIPT`) и только в коротком письме:
#: «We've received your request and will get back to you as quickly as possible».
ANSWER_LATER: Table = {
    "en": (
        r"\bwill\s+(?:get\s+back\s+to\s+you|respond(?:\s+to\s+you)?|reply(?:\s+to\s+you)?|"
        r"be\s+in\s+touch|contact\s+you|assist\s+you)\b[^.!?]{0,60}?\b(?:as\s+soon\s+as\s+"
        r"(?:possible|we\s+can)|shortly|as\s+quickly\s+as\s+possible|within\s+\d+|in\s+the\s+next\s+\d+)",
    ),
    "de": (
        r"\b(?:melden\s+uns|antworten\s+ihnen|uns\s+(?:bei\s+ihnen\s+)?melden)\b[^.!?]{0,40}?"
        r"\b(?:so\s+schnell\s+wie\s+möglich|schnellstmöglich|in\s+kürze|zeitnah|innerhalb\s+von\s+\d+)",
    ),
    "fr": (
        r"\b(?:répondrons|reviendrons\s+vers\s+vous|recontacterons)\b[^.!?]{0,40}?"
        r"\b(?:dans\s+les\s+(?:plus\s+)?brefs\s+délais|rapidement|sous\s+\d+|au\s+plus\s+vite|"
        r"dès\s+que\s+possible)",
    ),
    "es": (
        r"\b(?:responderemos|contactaremos)\b[^.!?]{0,40}?\b(?:lo\s+antes\s+posible|en\s+breve|"
        r"a\s+la\s+mayor\s+brevedad|en\s+las\s+próximas\s+\d+)",
    ),
    "it": (
        r"\b(?:risponderemo|ricontatteremo)\b[^.!?]{0,40}?\b(?:al\s+più\s+presto|a\s+breve|"
        r"il\s+prima\s+possibile|entro\s+\d+)",
    ),
    "pt": (
        r"\b(?:responderemos|retornaremos|entraremos\s+em\s+contato)\b[^.!?]{0,40}?"
        r"\b(?:o\s+mais\s+(?:breve|rápido)\s+possível|em\s+breve|assim\s+que\s+possível|em\s+até\s+\d+)",
    ),
    "nl": (
        r"\b(?:nemen|zullen)\b[^.!?]{0,30}?\b(?:zo\s+snel\s+mogelijk|spoedig|binnen\s+\d+)"
        r"[^.!?]{0,30}?\bcontact\b",
    ),
    "pl": (
        r"\b(?:odpowiemy|skontaktujemy\s+się)\b[^.!?]{0,40}?\b(?:najszybciej\s+jak|wkrótce|"
        r"niezwłocznie|w\s+ciągu\s+\d+)",
    ),
    "ru": (
        r"\b(?:ответим|свяжемся\s+с\s+вами)\b[^.!?]{0,40}?\b(?:в\s+ближайшее\s+время|"
        r"как\s+можно\s+скорее|в\s+течение\s+\d+)",
    ),
}

#: Оценка разговора в чате поддержки — письмо из одной строки: «Rate your conversation»
#: (проверка прода 25.11.2026). Решает только в очень коротком письме (`classify`).
CHAT_RATING: Table = {
    "en": (r"\b(?:rate|review)\s+(?:your|this|the)\s+(?:conversation|chat|experience)\b",),
    "de": (r"\bbewerten\s+sie\s+(?:dieses|das|ihr)\s+(?:gespräch|chat)\b",),
    "fr": (r"\b(?:évaluez|notez)\s+(?:cette|votre)\s+(?:conversation|discussion)\b",),
    "es": (r"\b(?:valora|califica|evalúa)\s+(?:esta|tu|su)\s+(?:conversación|chat)\b",),
    "it": (r"\b(?:valuta|vota)\s+(?:questa|la\s+tua)\s+(?:conversazione|chat)\b",),
    "pt": (r"\b(?:avalie|classifique)\s+(?:esta|sua|a)\s+(?:conversa|conversação)\b",),
    "nl": (r"\bbeoordeel\s+(?:dit|uw|je)\s+(?:gesprek|chat)\b",),
    "pl": (r"\boceń\s+(?:tę|swoją)\s+(?:rozmowę|konwersację)\b",),
    "ru": (r"\bоцените\s+(?:этот\s+|наш\s+)?(?:диалог|разговор|чат)\b",),
}

SERVICE_PROMISE_RE = compiled(SERVICE_PROMISE)
ANSWER_LATER_RE = compiled(ANSWER_LATER)
CHAT_RATING_RE = compiled(CHAT_RATING)
