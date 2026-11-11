/**
 * Вкладка «Не привязаны»: ответы, которые приём сохранил, но не соотнёс
 * ни с одним нашим письмом.
 *
 * **Сохранённый и невидимый ответ — тот же «донор не ответил».** Приём не
 * выбрасывал такие ответы с первого дня, а показать их было негде: до
 * 28.09.2026 их не видел никто, включая ответ на пробное письмо, которым
 * проверяют, что ответы вообще доходят.
 *
 * **Причина видна без раскрытия.** По ней решают, что делать: «ответ на
 * пробное письмо» — всё работает, «подпись не сошлась» у всех подряд —
 * сменили секрет, «метки нет» — отправителя ищут по адресу и тексту. Слова —
 * сервера (`replies/unbound.py`): второй экземпляр на фронте разошёлся бы
 * с ними на первой правке.
 *
 * **В строке — метка в несколько слов, целиком причина — в «i»** (11.11.2026). До этого
 * строка несла слова целиком: около двадцати пяти слов, на телефоне четыре-пять строк, и почти
 * у всех ответов слово в слово одни и те же (разведка экранов 10.11.2026). Метку тоже называет
 * сервер (`reason_short`); сервер старше метки — в строке слова целиком, как было. Что вообще
 * попадает на вкладку — в «i» у заголовка экрана (`UnboundAbout`), а не абзацем над списком.
 *
 * **Строка раскрывается в письмо целиком** — тем же видом, что ответ
 * в карточке диалога, с вложениями и «Скачать» (`ReplyFiles`). Раскрытие —
 * в два такта (`runs/Unfold`), закрытое не отрисовывается. Щелчок по строке
 * раскрывает её, кнопка со стрелкой — то же с клавиатуры; выделение текста
 * щелчком не считается: адрес отправителя копируют, чтобы искать донора.
 *
 * Страница — на сервере и в адресе (`?tab=unbound&page=2`), размер называет
 * сервер; страница за концом уводит на последнюю.
 */

import { Alert, Badge, Box, Button, Group, Loader, Stack, Text } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { REPLY_KINDS } from '../api/labels';
import type { UnboundReply } from '../api/unbound';
import { InfoHint } from '../components/InfoHint';
import { PageSwitch } from '../components/PageSwitch';
import { formatDateTime, formatNumber } from '../format';
import { Unfold } from '../runs/Unfold';
import { ReplyFiles } from './ReplyFiles';
import { useUnboundPage } from './threadTabs';

/**
 * Причина в строке ответа: метка сервера, а слова целиком — в «i» сразу за ней.
 *
 * Значок стоит в самой строке, за последним словом, а не колонкой справа: колонка отнимала у
 * текста 26 px, а значок висел посреди высоты причины, вставшей на телефоне в две-три строки.
 * Неразрывный пробел держит значок при последнем слове — один на своей строке он не остаётся.
 */
function Reason({ reply }: { reply: UnboundReply }) {
  const short = reply.reason_short;
  return (
    <Text size="sm" className="unboundReason">
      <Text span fw={500} inherit>
        Почему не привязан:
      </Text>{' '}
      {short ?? reply.reason_text}
      {short !== undefined && (
        <>
          {'\u00a0'}
          {/* Нажатие на «i» и внутри его окошка строку не раскрывает: окошко — потомок
              строки, и нажатие в нём всплывает к ней, хотя нарисовано поверх страницы. */}
          <Box
            component="span"
            className="unboundReasonHint"
            onClick={(event) => event.stopPropagation()}
          >
            <InfoHint
              name={`Причина целиком — ответ от ${reply.from_email ?? 'неназванного отправителя'}`}
              width={340}
              interactive
            >
              <Text size="sm">{reply.reason_text}</Text>
            </InfoHint>
          </Box>
        </>
      )}
    </Text>
  );
}

function UnboundRow({ reply }: { reply: UnboundReply }) {
  const [open, setOpen] = useState(false);
  const bodyId = `unbound-reply-${reply.id}`;
  const kind = REPLY_KINDS[reply.kind];
  const toggle = () => setOpen((was) => !was);

  return (
    <li className="unboundRow">
      <Group
        className="unboundHead glassSlot"
        wrap="nowrap"
        align="flex-start"
        gap="xs"
        onClick={() => {
          if (!window.getSelection()?.toString()) toggle();
        }}
      >
        {/* `aria-expanded` и `aria-controls` — не украшение: без них программа
            чтения видит кнопку без состояния, а тест не отличает раскрытое
            от свёрнутого. */}
        <Button
          variant="subtle"
          size="compact-sm"
          px={6}
          className="unboundToggle"
          aria-label={open ? 'Свернуть ответ' : 'Показать ответ целиком'}
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={(event) => {
            event.stopPropagation();
            toggle();
          }}
        >
          <IconChevronDown
            size={16}
            aria-hidden
            className="unboundChevron"
            data-open={open || undefined}
          />
        </Button>

        <Stack gap={4} className="unboundLines">
          {/* На телефоне дата уходит строкой ниже, а не сжимает адрес. */}
          <Group
            justify="space-between"
            align="baseline"
            gap={4}
            style={{ columnGap: 'var(--mantine-spacing-md)' }}
          >
            <Text fw={500} c="var(--ink)" className="cellName unboundFrom">
              {reply.from_email ?? 'отправитель не назван'}
            </Text>
            <Text size="xs" c="dimmed" className="unboundWhen">
              {formatDateTime(reply.received_at)}
            </Text>
          </Group>
          <Text size="xs" c="dimmed" className="cellName unboundTo">
            {reply.to.length > 0
              ? `на ${reply.to.join(', ')}`
              : 'адрес, на который пришло, не записан'}
          </Text>
          <Group gap="xs" align="center" mt={2}>
            <Badge variant="light" color={kind.color}>
              {kind.title}
            </Badge>
            <Text size="sm" fw={500} className="cellName unboundSubject">
              {reply.subject || 'без темы'}
            </Text>
            {reply.attachments.length > 0 && (
              <Text size="xs" c="dimmed" className="unboundWhen">
                вложения · {formatNumber(reply.attachments.length)}
              </Text>
            )}
          </Group>
          <Text size="sm" c="dimmed" lineClamp={2} className="cellName unboundPreview">
            {reply.preview || 'Текста в письме нет.'}
          </Text>
          <Reason reply={reply} />
        </Stack>
      </Group>

      <Unfold open={open} className="unboundBody hairline">
        <div id={bodyId}>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {reply.text || 'Текста в письме нет.'}
          </Text>
          {/* Прайс приходит файлом чаще, чем текстом. Файл скачивается, но не
              показывается внутри страницы — он пришёл снаружи. */}
          <ReplyFiles replyId={reply.id} files={reply.attachments} />
        </div>
      </Unfold>
    </li>
  );
}

interface Props {
  page: number;
  onPage: (page: number, replace?: boolean) => void;
}

export function UnboundReplies({ page, onPage }: Props) {
  const { data, isLoading, error, isPlaceholderData } = useUnboundPage(page);
  const pages = data === undefined ? 1 : Math.max(1, Math.ceil(data.total / data.limit));
  const settled = data !== undefined && !isPlaceholderData;

  // Страница за концом — старая ссылка или последний ответ последней страницы,
  // которого больше нет: экран уходит на последнюю, заменяя адрес.
  useEffect(() => {
    if (settled && page > pages) onPage(pages, true);
  }, [settled, page, pages, onPage]);

  if (isLoading) return <Loader aria-label="Загружаем непривязанные ответы" size="sm" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Непривязанные ответы не загрузились">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined || data.total === 0) {
    // Что сюда попадает — в «i» у заголовка экрана (`UnboundAbout`); здесь — чего нет.
    return (
      <Text size="sm" fw={500} className="unboundNote" py="sm">
        Непривязанных ответов нет.
      </Text>
    );
  }

  return (
    <Stack gap="sm">
      {/* Строки прежней страницы, пока едет новая, приглушены: они не той
          страницы. */}
      <Stack
        component="ul"
        gap={0}
        aria-label="Непривязанные ответы"
        className="unboundList staleRows"
        data-stale={isPlaceholderData || undefined}
        aria-busy={isPlaceholderData || undefined}
      >
        {data.rows.map((reply) => (
          <UnboundRow key={reply.id} reply={reply} />
        ))}
      </Stack>
      <PageSwitch
        label="Страницы непривязанных ответов"
        page={page}
        pages={pages}
        onChange={onPage}
        pt="xs"
      />
    </Stack>
  );
}
