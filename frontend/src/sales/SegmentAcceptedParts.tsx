/**
 * Из чего складываются принятые — под кнопкой «Найти адреса ещё N из M принятых» (тест продаж
 * 22.10.2026: «Принято 99», а на кнопке «· 8» — откуда 8, с экрана было не понять). Строками на
 * виду, а не подсказкой по наведению: на телефоне наведения нет.
 *
 * **Части — числами отчёта прогона, без счёта на экране** (`SegmentRunDetail`):
 * - с лидом — `summary.leads`: лиды прогона по ссылке сайта, а она есть только у принятого (отмена
 *   решения гасит ссылку, пересуд сайт с лидом не двигает);
 * - без адреса — `summary.no_address`, почему — строками `no_address`, теми же словами, что в «Почему
 *   без адреса»; из них ждут повтора — сумма и ближайший срок строк «Ждут адреса» (`waits`), словами
 *   срока блока (`termWords`). Это те же сайты («сайт не ответил», платный поиск «не спросили»),
 *   поэтому они — внутри «без адреса», а не отдельным слагаемым;
 * - возьмёт кнопка — `waiting_address`, условием прохода (`segment_report._addresses`).
 *
 * **«Из M принятых» — только когда части сходятся с «Принято».** По определениям сервера сходятся
 * не всегда: сайт без адреса, у которого настал срок повтора («повтор следующим проходом», «сайт не
 * ответил» после срока, исход старше срока годности) или на домене которого уже есть живой адрес, —
 * и в «без адреса», и в числе кнопки. Тогда части — без «из M», а у числа кнопки сказано, почему
 * сумма больше. Недостающее вычитанием не досчитывается: расхождение по другой причине остаётся
 * видным, а не прячется в подогнанном числе.
 */

import { List, Stack, Text } from '@mantine/core';

import { formatNumber } from '../format';
import type { SegmentRunDetail } from './segmentApi';
import { termWords } from './termWords';

/** Часть принятых строкой списка и, если есть, «из них» — вложенной строкой. */
export interface AcceptedPart {
  key: 'leads' | 'no_address' | 'take';
  text: string;
  sub: string | null;
}

/** Принятые по частям: заголовок («Из M принятых:» — только когда части сходятся), части и кто
 *  ищет сайты, принятые после прогона. */
export interface AcceptedView {
  whole: boolean;
  head: string;
  parts: AcceptedPart[];
  manual: string;
}

/** Тире и число — неразрывно с последним словом, как у строк ожидания (`waitLine`): на телефоне
 *  число одно на строке читалось оторванным. */
function counted(words: string, count: number): string {
  return `${words}\u00a0—\u00a0${formatNumber(count)}`;
}

/** Из «без адреса» ждут повтора — строки «Ждут адреса»: сколько и ближайший срок, словами блока. */
function retries(detail: SegmentRunDetail, now: Date): string | null {
  const rows = (detail.waits ?? []).filter((wait) => wait.kind === 'address');
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  if (total === 0) return null;
  const nearest = rows.reduce<string | null>(
    (first, row) =>
      row.at !== null && (first === null || Date.parse(row.at) < Date.parse(first))
        ? row.at
        : first,
    null,
  );
  const said = counted('из них ждут повтора', total);
  return nearest === null ? said : `${said}, ближайший ${termWords(nearest, now)}`;
}

function noAddress(detail: SegmentRunDetail, now: Date): AcceptedPart | null {
  const total = detail.summary.no_address;
  if (total === 0) return null;
  // Слова причин могут нести запятую («адрес не ищем, лида не заводим») — между ними «;».
  const why = detail.no_address.map((row) => counted(row.words, row.count)).join('; ');
  const text =
    why === '' ? counted('без адреса', total) : `${counted('без адреса', total)}: ${why}`;
  return { key: 'no_address', text, sub: retries(detail, now) };
}

/**
 * Принятые по частям — числами сервера. `button` — у учётки есть кнопка (право «run»); без неё
 * адреса найдёт продолжение прогона, которое запустит сотрудник с правом.
 */
export function acceptedParts(
  detail: SegmentRunDetail,
  button: boolean,
  now: Date = new Date(),
): AcceptedView {
  const { summary } = detail;
  const sum = summary.leads + summary.no_address + detail.waiting_address;
  const whole = sum === summary.accepted;
  const taker = button ? 'кнопка' : 'продолжение прогона';
  const take = counted(`возьмёт ${taker}`, detail.waiting_address);
  const parts = [
    summary.leads > 0
      ? { key: 'leads' as const, text: counted('с лидом', summary.leads), sub: null }
      : null,
    noAddress(detail, now),
    {
      key: 'take' as const,
      // Больше «Принято» сумма бывает только так: часть «без адреса» берёт и кнопка.
      text:
        sum > summary.accepted
          ? `${take}, среди них и сайты без адреса: у них настал срок повтора или на домене уже есть адрес`
          : take,
      sub: null,
    },
  ].filter((part): part is AcceptedPart => part !== null);
  return {
    whole,
    head: whole ? `Из ${formatNumber(summary.accepted)} принятых:` : 'Принятые:',
    parts,
    manual: `Сайты, принятые вручную после прогона, ищет ${button ? 'эта кнопка' : 'продолжение прогона'}: поиск адреса платный, его запускает человек.`,
  };
}

/** Расшифровка под кнопкой продолжения: части принятых списком и кто ищет принятых после прогона. */
export function AcceptedParts({ detail, button }: { detail: SegmentRunDetail; button: boolean }) {
  const view = acceptedParts(detail, button);
  return (
    <Stack gap={2}>
      <Text size="sm">{view.head}</Text>
      <List size="sm" spacing={2} aria-label="Принятые по частям">
        {view.parts.map((part) => (
          <List.Item key={part.key}>
            {part.text}
            {part.sub !== null && (
              <List size="sm" withPadding aria-label="Без адреса — ждут повтора">
                <List.Item>{part.sub}</List.Item>
              </List>
            )}
          </List.Item>
        ))}
      </List>
      <Text size="sm" c="dimmed">
        {view.manual}
      </Text>
    </Stack>
  );
}
