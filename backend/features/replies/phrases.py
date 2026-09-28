"""Фразы, по которым узнаётся служебное письмо, — на языках рынков.

Рынков восемь, и доноры отвечают на своих: английский, немецкий,
французский, испанский, итальянский, португальский, нидерландский,
польский, плюс русский. Словарь, знающий два языка из девяти, принимает
автоответ немца за живой ответ, а отписку поляка — за вопрос про цену.

**Фраза, а не слово.** Живой ответ пишет те же слова, что и автомат,
только о другом: тревел-блог называет себя словом «vacation», блог
о почтовых сервисах пишет «auto-reply» о своей статье, а «не пишите
статьи про казино» начинается так же, как «не пишите нам больше». Слово
ловит всех троих, фраза — только того, кто говорит о себе. Поэтому
у фразы отсутствия есть лицо и срок («I am out of the office until…»),
у просьбы об отписке — лицо и то, о чём просят («remove me from your
list»), а у мёртвого ящика — «больше не»: адрес был и перестал.

**Ошибки здесь несимметричны.** Живой ответ, принятый за служебное
письмо, теряет цену молча: автоответ и отписка человека не ждут, а отказ
доставки хоронит адрес. Служебное письмо, принятое за живое, стоит
минуты разбора. Поэтому фраза, которую пишет и автомат, и живой человек,
в словарь не попадает.

Словари лежат по языкам: видно, какой язык чем покрыт, а проверка
«язык не забыт» — одна строка теста. Между словами везде `\\s+`, а не
пробел: текст письма переносится по ширине, и фраза рвётся на две строки.
"""

from __future__ import annotations

import re

#: Языки рынков. Каждый словарь обязан знать их все.
LANGUAGES = ("en", "de", "fr", "es", "it", "pt", "nl", "pl", "ru")

_Table = dict[str, tuple[str, ...]]

#: Человека нет на месте. Ищется в тексте и в теме.
#:
#: Слово «auto-reply» здесь только в самоназвании («this is an automatic
#: reply»): блог о почтовых сервисах пишет его о своей статье, и живой
#: ответ без цены ушёл бы в автоответы, где его не прочтёт никто.
#:
#: Прошедшее время отсекается там, где за фразой стоит срок: «I was out
#: of the office», «ich war im Urlaub bis gestern», «я был в отпуске до
#: вчера» пишет человек, который вернулся и отвечает.
ABSENCE: _Table = {
    "en": (
        # Не «was out of the office» (ответил, вернувшись) и не «out of office
        # messages» (тема статьи).
        r"(?<!\bwas\s)(?<!\bwere\s)(?<!\bbeen\s)\bout\s+of\s+(?:the\s+)?office\b"
        r"(?!\s*(?:messages?|repl(?:y|ies)|responders?|notices?|templates?|e-?mails?|settings?)\b)",
        # «Out» — только со сроком сразу после: «I'm out of slots until
        # November» — живой отказ, а не отпуск.
        r"\bi(?:['’]m|\s+am|\s+will\s+be|['’]ll\s+be)\s+(?:currently\s+|now\s+)?(?:away|out|off)"
        r"\s+(?:until|till|through|from)\b",
        r"\bi(?:['’]m|\s+am|\s+will\s+be|['’]ll\s+be)\s+(?:currently\s+|now\s+)?away\s+from\s+"
        r"(?:the\s+|my\s+)?(?:office|desk)\b",
        r"\bi(?:['’]m|\s+am)\s+(?:currently\s+)?travell?ing\b[^\n.!?]{0,40}?\b(?:until|till|with\s+limited)\b",
        r"\bi(?:['’]m|\s+am|\s+will\s+be|['’]ll\s+be)\s+(?:currently\s+|now\s+)?on\s+(?:vacation|holidays?"
        r"|(?:annual\s+|parental\s+|maternity\s+|paternity\s+|sick\s+)?leave|a\s+business\s+trip)\b",
        r"\bon\s+(?:annual\s+|parental\s+|maternity\s+|paternity\s+|sick\s+)?leave\s+(?:until|till|through|from)\b",
        r"\b(?:limited|no|restricted|intermittent)\s+access\s+to\s+(?:my\s+)?e-?mails?\b",
        r"\bthis\s+is\s+an?\s+(?:automatic|automated|auto-generated|auto)[\s-]*(?:reply|response|message)\b",
    ),
    "de": (
        r"\babwesenheitsnotiz\b",
        r"\bdies\s+ist\s+eine\s+automatische\s+(?:antwort|nachricht|e-?mail|benachrichtigung)\b",
        r"\bnicht\s+im\s+büro\b",
        r"\bau(?:ß|ss)er\s+haus\b",
        r"\b(?:bin|sind)\b[^\n!?]{0,60}?\b(?:im\s+urlaub|abwesend|in\s+elternzeit|auf\s+dienstreise)\b",
        r"(?<!\bwar\s)(?<!\bwaren\s)\b(?:im\s+urlaub|abwesend|auf\s+dienstreise)\s+bis\b",
        r"\bwieder\s+(?:im\s+büro|erreichbar)\b",
        r"\b(?:keinen|eingeschränkten)\s+zugriff\s+auf\s+(?:meine\s+)?e-?mails?\b",
    ),
    "fr": (
        r"\babsente?\s+du\s+bureau\b",
        r"\bhors\s+du\s+bureau\b",
        r"\bceci\s+est\s+une?\s+(?:réponse|message)\s+automatique\b",
        r"\bje\s+suis\s+(?:actuellement\s+)?(?:absente?|en\s+congés?|en\s+vacances|en\s+déplacement)\b",
        r"(?<!\bétais\s)(?<!\bétions\s)(?<!\bété\s)\b(?:absente?|en\s+congés?|en\s+vacances)"
        r"\s+(?:jusqu['’](?:au|à)|du)\b",
        r"\bde\s+retour\s+(?:le|au\s+bureau|à\s+partir\s+du)\b",
        r"\bmessage\s+d['’]absence\b",
        r"\baccès\s+(?:limité|restreint)\s+à\s+(?:mes\s+|ma\s+)?(?:e-?mails?|courriels?|messagerie)\b",
    ),
    "es": (
        r"(?<!\bestuve\s)(?<!\bestaba\s)(?<!\bestuvimos\s)\bfuera\s+de\s+(?:la\s+)?oficina\b",
        r"\best[ae]\s+es\s+una?\s+(?:respuesta|mensaje)\s+automátic[oa]\b",
        r"\bmensaje\s+de\s+ausencia\b",
        r"\bestoy\s+(?:actualmente\s+)?(?:fuera|ausente|de\s+vacaciones|de\s+viaje|de\s+permiso)\b",
        r"(?<!\bestuve\s)(?<!\bestaba\s)\b(?:ausente|de\s+vacaciones|de\s+viaje)\s+hasta\b",
        r"\bestaré\s+(?:de\s+vuelta|fuera|ausente|de\s+vacaciones)\b",
        r"\bacceso\s+limitado\s+al?\s+(?:mi\s+)?(?:correo|e-?mail)\b",
    ),
    "it": (
        r"(?<!\bero\s)(?<!\beravamo\s)(?<!\bstato\s)(?<!\bstata\s)\bfuori\s+(?:ufficio|sede)\b",
        r"\bquest[ao]\s+è\s+una?\s+(?:risposta|messaggio)\s+automatic[ao]\b",
        r"\bsono\s+(?:attualmente\s+)?(?:assente|in\s+ferie|in\s+vacanza|in\s+trasferta)\b",
        r"(?<!\bero\s)(?<!\bstato\s)(?<!\bstata\s)\b(?:assente|in\s+ferie|in\s+vacanza)\s+(?:fino|sino|dal)\b",
        r"\b(?:rientrerò|sarò\s+di\s+nuovo|tornerò)\s+in\s+ufficio\b",
        r"\baccesso\s+limitato\s+(?:alla\s+posta|alle\s+e-?mail)\b",
    ),
    "pt": (
        r"(?<!\bestive\s)(?<!\bestava\s)(?<!\bestivemos\s)\bfora\s+do\s+escritório\b",
        r"\best[ae]\s+é\s+uma\s+(?:resposta|mensagem)\s+automática\b",
        r"\bmensagem\s+de\s+ausência\b",
        r"\bestou\s+(?:atualmente\s+)?(?:ausente|de\s+férias|em\s+viagem|de\s+licença)\b",
        r"(?<!\bestive\s)(?<!\bestava\s)\b(?:ausente|de\s+férias|em\s+viagem)\s+até\b",
        r"\bestarei\s+(?:de\s+volta|ausente|fora|de\s+férias)\b",
        r"\bacesso\s+limitado\s+ao?s?\s+(?:e-?mails?|correio)\b",
    ),
    "nl": (
        r"\bik\s+ben\b[^\n!?]{0,40}?\b(?:afwezig|op\s+vakantie|met\s+vakantie|niet\s+op\s+kantoor"
        r"|met\s+verlof)\b",
        r"(?<!\bwas\s)(?<!\bwaren\s)\b(?:afwezig|op\s+vakantie|met\s+vakantie|met\s+verlof)\s+(?:tot|t/m|van)\b",
        r"\bdit\s+is\s+een\s+automatisch\s+(?:antwoord|bericht|gegenereerd\s+bericht)\b",
        r"\bafwezigheids(?:bericht|melding)\b",
        r"\b(?:weer|terug)\s+(?:aanwezig|op\s+kantoor)\b",
        r"\bbuiten\s+kantoor\b",
    ),
    "pl": (
        r"\bpoza\s+biurem\b",
        r"\bto\s+jest\s+(?:automatyczna\s+(?:odpowiedź|wiadomość)|(?:odpowiedź|wiadomość)\s+automatyczna)\b",
        r"\bjestem\b[^\n!?]{0,40}?\b(?:nieobecn[ya]|na\s+urlopie|na\s+zwolnieniu|w\s+delegacji)\b",
        r"(?<!\bbyłem\s)(?<!\bbyłam\s)(?<!\bbyliśmy\s)\b(?:nieobecn[ya]|na\s+urlopie)\s+(?:do|od|w\s+dniach)\b",
        r"\bbędę\s+(?:ponownie\s+)?dostępn[ya]\s+(?:od|po|w|we)\b",
        r"\bograniczony\s+dostęp\s+do\s+(?:poczty|e-?maila|skrzynki)\b",
    ),
    "ru": (
        r"\bавтоответ\b",
        r"\bэто\s+автоматическ(?:ий|ое)\s+(?:ответ|сообщение|письмо|уведомление)\b",
        r"\b(?:письмо|сообщение)\s+(?:сформировано|отправлено|создано)\s+автоматически\b",
        r"(?<!\bбыл\s)(?<!\bбыла\s)(?<!\bбыли\s)\bв\s+отпуске\s+(?:до|по|с)\b",
        r"\bнахожусь\s+в\s+(?:отпуске|командировке)\b",
        r"\bменя\s+нет\s+на\s+месте\b",
        r"\b(?:не\s+в\s+офисе|вне\s+офиса)\b",
        r"(?<!\bбыл\s)(?<!\bбыла\s)(?<!\bбыли\s)\bв\s+командировке\s+(?:до|по)\b",
        r"\bбуду\s+(?:на\s+связи|в\s+офисе|доступ\w*)\s+(?:с|со|после)\b",
    ),
}

#: Как автоответчик называет себя в теме: «Automatic reply: …»,
#: «Abwesenheitsnotiz: …». Тема письма — наша, с «Re:» спереди, и всё
#: лишнее в ней дописал автомат, поэтому здесь можно и одно слово: живой
#: ответ тему не переписывает. Адрес сайта из темы убирается до поиска.
AUTO_SUBJECT: _Table = {
    "en": (
        r"\bauto(?:matic|mated)?[\s-]*(?:reply|response|responder)\b",
        r"\bautoreply\b",
        r"\bon\s+(?:vacation|holidays?|leave)\b",
    ),
    "de": (r"\bautomatische\s+antwort\b", r"\babwesen(?:d|heit\w*)\b", r"\bim\s+urlaub\b"),
    "fr": (r"\bréponse\s+automatique\b", r"\babsen(?:te?|ce)\b", r"\ben\s+congés?\b"),
    "es": (r"\brespuesta\s+automática\b", r"\bausen(?:te|cia)\b", r"\bde\s+vacaciones\b"),
    "it": (r"\brisposta\s+automatica\b", r"\bassen(?:te|za)\b", r"\bin\s+ferie\b"),
    "pt": (r"\bresposta\s+automática\b", r"\baus(?:ente|ência|encia)\b", r"\bde\s+férias\b"),
    "nl": (r"\bautomatisch\s+antwoord\b", r"\bafwezig\w*\b", r"\b(?:op|met)\s+vakantie\b"),
    "pl": (
        r"\b(?:automatyczna\s+odpowiedź|odpowiedź\s+automatyczna|autoodpowiedź)\b",
        r"\b(?:nieobecn|urlop)\w*",
    ),
    "ru": (r"\bавтоматический\s+ответ\b", r"\bнет\s+на\s+месте\b", r"\bотпуск\w*\b"),
}

#: «Your mailing list», «the database» — то, откуда просят убрать.
_A_LIST = (
    r"(?:your|the|this|all|any)\s+(?:\S+\s+){0,2}?"
    r"(?:list|lists|database|mailing|newsletter|contacts|records)\b"
)

#: Просьба больше не писать. У каждой есть адресат («me», «нам») и то,
#: о чём просят. «Не пишите нам про казино» — условие публикации, а не
#: отписка: где за просьбой может стоять тема, после неё допускается
#: только конец фразы или «больше», а не что угодно.
UNSUBSCRIBE: _Table = {
    "en": (
        r"\bunsubscribe\s+(?:me|us)\b",
        r"\bplease\s+unsubscribe\b",
        # «Remove me from CC» — передача другому, а не отписка: после «from»
        # нужен список.
        r"\bremove\s+(?:me|us)\b(?=\s*(?:$|[.!,;]|please|from\s+" + _A_LIST + r"))",
        r"\bremove\s+(?:my|our)\s+(?:e-?mail(?:\s+address)?|address|contact(?:\s+details)?|details)"
        r"\s+(?:from|off)\b",
        r"\btake\s+(?:me|us|my\s+(?:e-?mail|address)|our\s+(?:e-?mail|address))\s+off\b"
        r"(?=\s*(?:$|[.!,;]|please|" + _A_LIST + r"))",
        r"\bstop\s+(?:e-?mailing|contacting|messaging|spamming|bothering)\s+(?:me|us)\b",
        r"\bstop\s+(?:sending|writing)\s+(?:to\s+)?(?:me|us)\b"
        r"(?=\s*(?:$|[.!,;]|e-?mails|messages|offers|these|this|spam|again|anymore))",
        r"\b(?:do\s+not|don['’]t|dont|never)\s+(?:contact|e-?mail|write\s+to|message)\s+(?:me|us)\b"
        r"(?=\s*(?:$|[.!,;]|again|anymore|any\s+more|ever|further|in\s+(?:the\s+)?future|please))",
        r"\bno\s+more\s+(?:e-?mails|spam)\b",
        r"\bopt\s+(?:me|us)\s+out\b",
    ),
    "de": (
        r"\bmelden\s+sie\s+(?:mich|uns)\b[^\n.]{0,30}?\bab\b",
        r"\b(?:entfernen|löschen|streichen)\s+sie\s+(?:mich|uns|(?:meine|unsere)\s+(?:adresse|e-?mail\S*))\b",
        r"\btragen\s+sie\s+(?:mich|uns)\b[^\n.]{0,30}?\baus\b",
        r"\baus\s+(?:ihrem|ihrer)\s+(?:e-?mail-?)?(?:verteiler|mailing-?liste|liste|datenbank|kartei)\b",
        r"\baus\s+dem\s+(?:e-?mail-?)?verteiler\b",
        # «Keine Artikel über Casino» — условие публикации: нужны именно письма.
        r"\b(?:senden|schicken|schreiben)\s+sie\s+(?:mir|uns)\s+(?:bitte\s+)?keine\s+(?:weiteren\s+)?"
        r"(?:e-?mails|mails|nachrichten|angebote|werbung)\b(?!\s+(?:über|zu|zum|zur|mit|für)\b)",
        r"\bkeine\s+(?:weiteren\s+)?(?:e-?mails|mails|werbe-?mails|werbung)\s+mehr\b",
        # «Keine Nachrichten» — ещё и «никаких новостей»: нужен глагол получения.
        r"\bkeine\s+(?:weiteren\s+)?(?:e-?mails|mails|nachrichten|angebote|werbung)\b[^\n.]{0,30}?"
        r"\b(?:erhalten|bekommen|zugeschickt|zusenden)\b",
    ),
    "fr": (
        r"\bne\s+plus\s+(?:nous|me|m['’])\s*(?:contacter|écrire|solliciter)\b",
        r"\bne\s+pas\s+(?:nous|me|m['’])\s*(?:contacter|écrire|solliciter)\b"
        r"(?=\s*(?:$|[.!,;]|à\s+nouveau|de\s+nouveau))",
        r"\bne\s+(?:nous|me|m['’])\s*(?:contactez|écrivez|sollicitez)\s+plus\b",
        r"\barrêtez\s+de\s+(?:nous|me|m['’])\s*(?:écrire|contacter|solliciter)\b",
        # «Ne pas nous envoyer d'articles sur le casino» — условие, а не отписка:
        # «envoyer» считается только с письмами.
        r"\b(?:ne\s+(?:plus|pas)\s+(?:nous|me|m['’])\s*envoyer|ne\s+(?:nous|me|m['’])\s*envoyez\s+plus"
        r"|arrêtez\s+de\s+(?:nous|me|m['’])\s*envoyer)\s+(?:de\s+|d['’]|des\s+|vos\s+)?"
        r"(?:e-?mails?|mails?|messages?|courriels?|offres?|propositions?|sollicitations?)\b",
        r"\b(?:retirez|supprimez|enlevez|rayez)[-\s]+(?:moi|nous|(?:mon|notre)\s+(?:adresse|e-?mail))\b",
    ),
    "es": (
        r"\b(?:darme|darnos|denme|dennos|d[ée]me|d[ée]nos)\s+de\s+baja\b",
        r"\bno\s+(?:me|nos)\s+(?:escrib|contact|enví|molest)[ae]n?s?\b"
        r"(?=\s*(?:$|[.!,;]|más|nunca|otra\s+vez|de\s+nuevo|por\s+favor))",
        r"\b(?:elim[ií]n|qu[ií]t|b[oó]rr|s[aá]qu)(?:a|e|en)(?:me|nos)\b",
        r"\b(?:eliminen|borren|quiten|saquen)\s+(?:mi|nuestr[oa])\s+(?:correo|dirección|e-?mail|contacto)\b",
    ),
    "it": (
        r"\bcancellami\b",
        # «Non scriveteci articoli sul casinò» — условие: после просьбы только
        # конец фразы или «più».
        r"\bnon\s+(?:scriver|contattar|scrivete|contattate)(?:mi|ci)\b(?=\s*(?:$|[.!,;]|più|mai|ancora))",
        r"\bnon\s+(?:mi|ci)\s+(?:scriva|scrivete|contatti|contattate)\b(?=\s*(?:$|[.!,;]|più|mai))",
        r"\b(?:rimuovete|togliete|cancellate)(?:mi|ci)\b",
        r"\b(?:rimuovimi|toglimi)\b",
        r"\b(?:rimuovete|togliete|cancellate)\s+(?:il\s+)?(?:mio|nostro)\s+(?:indirizzo|contatto|e-?mail)\b",
    ),
    "pt": (
        r"\bnão\s+(?:me|nos)\s+(?:envie|contate|contacte|escreva|mande)m?\b"
        r"(?=\s*(?:$|[.!,;]|mais|novamente|de\s+novo|por\s+favor))",
        r"\b(?:remova|removam|retire|retirem|tire|tirem|exclua|excluam)-(?:me|nos)\b",
        r"\b(?:me|nos)\s+(?:remova|removam|tire|tirem|exclua|excluam)\s+da\b",
        r"\b(?:remova|removam|exclua|excluam)\s+(?:o\s+)?(?:meu|nosso)\s+(?:e-?mail|endereço|contato)\b",
    ),
    "nl": (
        r"\b(?:stuur|stuurt|mail|mailt)\s+(?:me|mij|ons)\s+(?:\S+\s+){0,2}?niet\s+meer\b",
        r"\b(?:stuur|stuurt|mail|mailt)\s+(?:me|mij|ons)\s+geen\s+(?:e-?mails?|mails?|berichten)\s+meer\b",
        r"\bverwijder\s+(?:me|mij|ons|(?:mijn|ons)\s+(?:e-?mail\S*|adres|gegevens))\s+(?:uit|van)\b",
        r"\bgeen\s+(?:e-?mails?|mails?|berichten)\s+meer\s+(?:ontvangen|sturen|toesturen)\b",
    ),
    "pl": (
        r"\bnie\s+(?:piszcie|pisz)\s+do\s+(?:mnie|nas)\b(?=\s*(?:$|[.!,;]|więcej|już|ponownie))",
        r"\bnie\s+(?:wysyłajcie|wysyłaj|przysyłajcie)\s+(?:mi|nam)\b(?=\s*(?:$|[.!,;]|więcej|już|żadnych))",
        # «Wypisać» — ещё и «выписать счёт»: нужен тот, кого вычёркивают, или список.
        r"\bwypis\w*\s+(?:mnie|nas)\b",
        r"\b(?:mnie|nas)\s+wypis\w*",
        r"\bwypis\w*\s+z\s+(?:listy|bazy|newslettera|mailingu|subskrypcji)\b",
        r"\busuń(?:cie)?\s+(?:mnie|nas|(?:mój|nasz)\s+(?:adres|e-?mail))\b",
        r"\bproszę\s+o\s+(?:usunięcie\s+(?:mnie|nas|mojego|naszego)|wypisanie)\b",
    ),
    "ru": (
        # «Больше не пишем про казино» — о себе, не просьба: только повелительное.
        r"\bбольше\s+не\s+пиши(?:те)?\b",
        r"\bне\s+пишите\s+(?:нам|мне)\b(?=\s*(?:$|[.!,;]|больше|более|впредь|никогда))",
        r"\bне\s+(?:надо|нужно)\s+(?:нам|мне)\s+(?:больше\s+)?писать\b(?=\s*(?:$|[.!,;]|больше))",
        r"\bотпишите\b",
        r"\b(?:хочу|хотим|прошу|как)\s+отписаться\b",
        r"\b(?:удалите|уберите|исключите)\s+(?:меня|нас|(?:наш|мой)\s+адрес|(?:нашу|мою)\s+почту)\b",
        r"\bне\s+беспокойте\s+(?:нас|меня)\b",
        r"\bпрекратите\s+(?:писать|рассылку|присылать|слать)\b",
    ),
}

#: Отписка одним словом: «Unsubscribe», «Abmelden», «Désinscription».
#: Так отвечают часто, но это же слово стоит в подвале рассылок — поэтому
#: строки со ссылкой или «нажмите» не считаются (`FOOTER_LINE`), а письмо
#: со служебными заголовками списка не считает слово вовсе.
UNSUBSCRIBE_WORD: _Table = {
    "en": (r"\bunsubscribe\b",),
    "de": (r"\babmelden\b",),
    "fr": (r"\bdésinscri\w*",),
    "es": (r"\bdar(?:se)?\s+de\s+baja\b",),
    "it": (r"\bdisiscri\w*",),
    "pt": (r"\bdescadastr\w*",),
    "nl": (r"\bafmelden\b", r"\buitschrijven\b"),
    "pl": (r"\brezygnacja\s+z\s+subskrypcji\b", r"\banuluj\s+subskrypcję\b"),
    "ru": (r"\bотписка\b",),
}

#: Строка подвала рассылки: ссылка или «нажмите здесь» рядом со словом.
FOOTER_LINE: _Table = {
    "en": (r"https?://", r"\bwww\.", r"\bclick\b"),
    "de": (r"\bklicken\b",),
    "fr": (r"\bcliquez\b",),
    "es": (r"\bhaga\s+clic\b", r"\bpulse\b"),
    "it": (r"\bclicca\b",),
    "pt": (r"\bclique\b",),
    "nl": (r"\bklik\b",),
    "pl": (r"\bkliknij\b",),
    "ru": (r"\bнажмите\b", r"\bперейдите\b"),
}

#: Ящика больше нет: «больше не читается», «человек ушёл». Смотрится
#: только у писем, которые подписал автомат, — в живом ответе «больше
#: не активна» говорит о странице, а не об ящике.
NO_LONGER: _Table = {
    "en": (
        r"\bno\s+longer\s+(?:in\s+use|monitored|active|valid|checked|read|maintained|in\s+service"
        r"|being\s+(?:monitored|checked|read|used))\b",
        r"\b(?:is|am|are)\s+no\s+longer\s+(?:with|at|employed|working|part\s+of)\b",
        r"\bno\s+longer\s+works?\s+(?:here|at|for|with)\b",
        r"\bha(?:s|ve)\s+left\s+(?:the\s+|our\s+)?(?:company|organi[sz]ation|business|team)\b",
        r"\b(?:address|mailbox|inbox|account)\s+(?:has\s+been|was|is)\s+(?:closed|deactivated|discontinued|disabled)\b",
    ),
    "de": (
        r"\bnicht\s+mehr\s+(?:in\s+betrieb|aktiv|gelesen|genutzt|verwendet|abgerufen|erreichbar"
        r"|im\s+unternehmen|für\s+uns\s+tätig|bei\s+uns)\b",
        r"\bwird\s+nicht\s+mehr\s+(?:gelesen|genutzt|verwendet|abgerufen|betreut|bearbeitet)\b",
        r"\bhat\s+(?:das\s+unternehmen|die\s+firma|uns)\s+verlassen\b",
    ),
    "fr": (
        r"\bn['’](?:est|sont)\s+plus\s+(?:utilisée?s?|consultée?s?|actives?|valides?|lue?s?|relevée?s?"
        r"|en\s+service|surveillée?s?)\b",
        r"\bne\s+fait\s+plus\s+partie\b",
        r"\bne\s+travaille\s+plus\b",
        r"\ba\s+quitté\s+(?:la\s+société|l['’]entreprise|l['’]organisation|notre\s+équipe)\b",
    ),
    "es": (
        r"\bya\s+no\s+(?:se\s+utiliza|se\s+usa|se\s+revisa|se\s+consulta|se\s+lee"
        r"|está\s+(?:activ[oa]|operativ[oa]|en\s+uso)|es\s+válid[oa])\b",
        r"\bya\s+no\s+(?:trabaja|forma\s+parte)\b",
        r"\bha\s+dejado\s+(?:la\s+empresa|la\s+compañía)\b",
    ),
    "it": (
        r"\bnon\s+è\s+più\s+(?:attiv[oa]|utilizzat[oa]|monitorat[oa]|in\s+uso|valid[oa]|consultat[oa]"
        r"|lett[oa]|in\s+servizio)\b",
        r"\bnon\s+viene\s+più\s+(?:lett[oa]|utilizzat[oa]|monitorat[oa]|consultat[oa]|controllat[oa])\b",
        r"\bnon\s+fa\s+più\s+parte\b",
        r"\bnon\s+lavora\s+più\b",
        r"\bha\s+lasciato\s+(?:l['’]azienda|la\s+società|l['’]organizzazione)\b",
    ),
    "pt": (
        r"\b(?:não\s+(?:é|está)\s+mais|já\s+não\s+(?:é|está))\s+(?:utilizad[oa]|usad[oa]|monitorad[oa]"
        r"|ativ[oa]|válid[oa]|em\s+uso|lid[oa]|verificad[oa])\b",
        r"\b(?:não\s+faz\s+mais|já\s+não\s+faz)\s+parte\b",
        r"\bnão\s+trabalha\s+mais\b",
        r"\bdeixou\s+(?:a\s+empresa|a\s+companhia|a\s+organização)\b",
    ),
    "nl": (
        r"\bniet\s+meer\s+(?:in\s+gebruik|gelezen|actief|beheerd|geldig|werkzaam|in\s+dienst)\b",
        r"\bwordt\s+niet\s+meer\s+(?:gelezen|gebruikt|beheerd|bekeken)\b",
        r"\bniet\s+langer\s+(?:in\s+gebruik|actief|werkzaam|in\s+dienst)\b",
        r"\bwerkt\s+niet\s+meer\s+(?:bij|voor)\b",
        r"\bheeft\s+(?:het\s+bedrijf|de\s+organisatie|ons)\s+verlaten\b",
    ),
    "pl": (
        r"\b(?:nie\s+jest\s+już|już\s+nie\s+jest)\s+(?:używan[ya]|monitorowan[ya]|aktywn[ya]"
        r"|obsługiwan[ya]|sprawdzan[ya]|czytan[ya]|pracownikiem|zatrudnion[ya])\b",
        r"\b(?:nie\s+pracuje\s+już|już\s+nie\s+pracuje)\b",
        r"\bod(?:szedł|eszła)\s+z\s+(?:firmy|pracy|zespołu|redakcji)\b",
    ),
    "ru": (
        r"\bбольше\s+не\s+(?:используется|обслуживается|отслеживается|читается|проверяется|работает"
        r"|действует|активен|активна|сотрудник|сотрудница)\b",
        r"\bуволил(?:ся|ась)\b",
        r"\b(?:ящик|адрес)\s+(?:был\s+)?(?:закрыт|отключ[её]н|удал[её]н)\b",
    ),
}

#: «Ящик не читается» без «больше». Так пишет и брошенный ящик, и робот
#: noreply о самом себе («this mailbox is not monitored, do not reply»)
#: в подтверждении заявки — поэтому у noreply эти фразы не считаются:
#: читают не его, а тот адрес, которому мы писали.
NOT_READ: _Table = {
    "en": (r"\b(?:is|are)\s+not\s+(?:being\s+)?(?:monitored|checked|read)\b",),
    "de": (r"\bwird\s+nicht\s+(?:gelesen|überwacht|betreut)\b",),
    "fr": (r"\bn['’]est\s+pas\s+(?:consultée?|lue?|surveillée?|relevée?)\b",),
    "es": (r"\bno\s+se\s+(?:revisa|consulta|lee|monitorea|supervisa)\b",),
    "it": (r"\bnon\s+(?:è|viene)\s+(?:monitorat[oa]|lett[oa]|controllat[oa]|presidiat[oa])\b",),
    "pt": (r"\bnão\s+é\s+(?:monitorad[oa]|lid[oa]|verificad[oa])\b",),
    "nl": (r"\bwordt\s+niet\s+(?:gelezen|gecontroleerd|bekeken)\b",),
    "pl": (r"\bnie\s+jest\s+(?:monitorowan[ya]|sprawdzan[ya]|czytan[ya])\b",),
    "ru": (r"\bне\s+(?:обслуживается|отслеживается|проверяется|читается)\b",),
}

#: Тема уведомления об отказе. Такую тему пишет почта, а не человек,
#: поэтому она смотрится у любого письма — но без адреса сайта: адрес
#: стоит в нашей же теме, и домен донора словарю не судья.
BOUNCE_SUBJECT: _Table = {
    "en": (
        r"\bundeliver(?:ed|able)\b|\bfailure\s+notice\b|\bnon-?delivery\b",
        # «(Delay)» — письмо ещё доставляется, хоронить адрес рано.
        r"\bdelivery\s+status\s+notification\b(?!\s*\((?:delay|success))",
        r"\b(?:mail\s+)?delivery\s+(?:has\s+)?(?:failed|failure|subsystem)\b",
        r"\breturned\s+(?:mail|to\s+sender)\b",
    ),
    "de": (r"\bunzustellbar\w*|\bnicht\s+zustellbar\b|\bzustellungsfehler\b",),
    "fr": (r"\bnon\s+(?:remis|distribuable)\b|\béchec\s+de\s+(?:la\s+)?(?:remise|livraison)\b",),
    "es": (r"\bno\s+se\s+puede\s+entregar\b|\bno\s+entregado\b|\berror\s+de\s+entrega\b",),
    "it": (r"\bnon\s+recapitabile\b|\bmancato\s+recapito\b|\bimpossibile\s+recapitare\b",),
    "pt": (r"\bnão\s+(?:é\s+possível\s+entregar|entregue)\b|\bfalha\s+na\s+entrega\b",),
    "nl": (r"\bonbestelbaar\b|\bniet\s+bezorgd\b|\bbezorging\s+mislukt\b",),
    "pl": (r"\bniedostarczaln\w+|\bniedostarczon[aey]\b|\bnie\s+można\s+dostarczyć\b",),
    "ru": (r"\bне\s+(?:доставлено|удалось\s+доставить)\b|\bнедоставл(?:енное|яемое)\b",),
}

#: Текст уведомления об отказе: коды и формулировки почтовых серверов.
#: Смотрится только у писем, которые написала почта (`classify`), —
#: поэтому здесь можно широко: «does not exist» в живом ответе говорит
#: о странице, а «550» — о цене.
BOUNCE_TEXT: _Table = {
    "en": (
        # «5.1.1», «550 5.1.1», «smtp; 550» — коды сервера.
        r"\b5\.[0-7]\.\d\b|\b5\d\d[\s-]+#?5\.\d{1,3}\.\d{1,3}\b|\bsmtp;\s*5\d\d\b",
        r"\b(?:user\s+unknown|unknown\s+user|no\s+such\s+(?:user|mailbox|recipient|address))\b",
        r"\bmailbox\s+(?:is\s+)?(?:unavailable|not\s+found|disabled)\b|\baddress\s+rejected\b",
        r"\bdoes\s+not\s+exist\b|\bundeliverable\b|\b(?:failed\s+permanently|permanent\s+(?:error|failure))\b",
        r"\bdelivery\s+(?:to\s+the\s+following\s+recipients?\s+)?(?:has\s+)?failed\b",
        r"\b(?:(?:could|can)\s*not\s+be|wasn['’]t|was\s+not|has\s+not\s+been|hasn['’]t\s+been)\s+delivered\b",
    ),
    "de": (
        r"\bunzustellbar\b|\b(?:konnte\s+)?nicht\s+zugestellt\b",
        r"\b(?:empfänger\w*\s+(?:unbekannt|existiert\s+nicht)|postfach\s+(?:existiert\s+nicht|nicht\s+verfügbar))\b",
    ),
    "fr": (
        r"\bnon\s+remis\b|\bn['’]a\s+pas\s+pu\s+être\s+(?:remis|distribué|livré)\b",
        r"\b(?:destinataire\s+(?:inconnu|introuvable)|adresse\s+introuvable)\b",
    ),
    "es": (
        r"\bno\s+se\s+(?:ha\s+)?pud[oi]\s+entregar\b",
        r"\b(?:destinatario\s+(?:desconocido|no\s+existe)|buzón\s+(?:no\s+existe|no\s+disponible))\b",
    ),
    "it": (
        r"\bnon\s+recapitabile\b|\bimpossibile\s+recapitare\b",
        r"\b(?:destinatario\s+(?:sconosciuto|inesistente)|casella\s+(?:inesistente|non\s+disponibile))\b",
    ),
    "pt": (
        r"\bnão\s+(?:foi\s+possível\s+entregar|foi\s+entregue)\b",
        r"\b(?:destinatário\s+(?:desconhecido|inexistente)|caixa\s+(?:postal\s+)?(?:inexistente|indisponível))\b",
    ),
    "nl": (
        r"\bonbestelbaar\b|\bkon\s+niet\s+worden\s+(?:bezorgd|afgeleverd)\b",
        r"\bontvanger\s+(?:onbekend|bestaat\s+niet)\b",
    ),
    "pl": (
        r"\bniedostarczon[aey]\b|\bnie\s+(?:można|udało\s+się)\s+dostarczyć\b",
        r"\badresat\s+(?:nieznany|nie\s+istnieje)\b",
    ),
    "ru": (
        r"\bне\s+(?:доставлено|удалось\s+доставить)\b|\bнедоставлен\w*",
        r"\b(?:адрес|ящик|пользователь)\w*\s+не\s+(?:существует|найден)\b",
    ),
}


def compiled(table: _Table) -> re.Pattern[str]:
    """Один шаблон на все языки словаря: совпадения ищутся одним проходом."""
    return re.compile("|".join(f"(?:{p})" for group in table.values() for p in group), re.I)


ABSENCE_RE = compiled(ABSENCE)
AUTO_SUBJECT_RE = compiled({lang: ABSENCE[lang] + AUTO_SUBJECT[lang] for lang in LANGUAGES})
UNSUBSCRIBE_RE = compiled(UNSUBSCRIBE)
UNSUBSCRIBE_WORD_RE = compiled(UNSUBSCRIBE_WORD)
FOOTER_LINE_RE = compiled(FOOTER_LINE)
NO_LONGER_RE = compiled(NO_LONGER)
NOT_READ_RE = compiled(NOT_READ)
BOUNCE_SUBJECT_RE = compiled(BOUNCE_SUBJECT)
BOUNCE_TEXT_RE = compiled(BOUNCE_TEXT)
