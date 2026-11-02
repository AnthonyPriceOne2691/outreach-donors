/**
 * «Обойти заново, 10 000» — подтверждение глубокого обхода (`crawl/deep.py`, 02.11.2026). Одно окно
 * на кнопку в строке донора, на отмеченных и на всех по фильтру: что будет — одними словами.
 *
 * **Окно говорит то, что дорого узнать потом**: такой обход идёт часами, а не минутами; разом их
 * не больше предела сервера, остальные ждут в очереди загрузки, и сколько это займёт на всех;
 * что сейчас уже идёт и ждёт; за что платим юнитами Ahrefs. Все числа — сервера (`deep` ответа
 * `/crawls/donors`), экран своих не держит: нет чисел (сервер старше экрана) — нет и кнопок.
 */

import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { CrawlDeepRules } from '../api/crawls';
import { formatNumber, plural } from '../format';
import { crawlEta } from './crawlFilters';
import { useStartDeep } from './crawlQueries';
import { spanWords } from './crawlWords';

/** Подпись кнопки в строке: «Заново, 10 000». */
export function deepLabel(rules: CrawlDeepRules): string {
  return `Заново, ${formatNumber(rules.max_pages)}`;
}

/** Заголовок окна: «Обойти заново, до 10 000 страниц?». */
export function deepTitle(rules: CrawlDeepRules): string {
  return `Обойти заново, до ${formatNumber(rules.max_pages)} страниц?`;
}

/** Сколько идёт один и все: срок одного и, если доноров много, всех при пределе разом. */
export function deepTiming(count: number, rules: CrawlDeepRules): string {
  const each =
    `Один донор обычно идёт около ${spanWords(rules.typical_sec)}, ` +
    `самое большее ${spanWords(rules.max_seconds)}.`;
  const many = crawlEta(count, rules.typical_sec, rules.max_running);
  const slots = `Разом идёт не больше ${formatNumber(rules.max_running)}`;
  if (count <= rules.max_running || many === null) return `${each} ${slots}.`;
  return `${each} ${slots}, остальные ждут в очереди загрузки: все пройдут ${many}.`;
}

/** Что уже идёт и ждёт — пусто, если никого: «очередь пуста» читалось бы обещанием начать сразу. */
export function deepNow(rules: CrawlDeepRules): string | null {
  if (rules.going === 0 && rules.waiting === 0) return null;
  const going = `Сейчас таких обходов идёт ${formatNumber(rules.going)} из ${formatNumber(rules.max_running)}`;
  if (rules.waiting === 0) return `${going}.`;
  return `${going}, ${plural(rules.waiting, 'ждёт', 'ждут', 'ждут')} ещё ${formatNumber(rules.waiting)}.`;
}

/** За что платим: юниты — только за новые домены, и кап их держит. */
export function deepCost(rules: CrawlDeepRules): string {
  return (
    'Новый обход заменит найденное прежним, решения по спорным и спрошенный DR сохранятся. ' +
    `Юниты Ahrefs — только за DR новых доменов «куплено» и «спорно»: ${formatNumber(rules.dr_units)} ` +
    `за домен, не меньше ${formatNumber(rules.dr_min_units)} за запрос. Месячный кап не пускает — ` +
    'DR подождёт, а обход дойдёт.'
  );
}

/** «Заново, 10 000 отмеченных · N»: чисел сервера нет — кнопки нет. */
export function DeepPicked({
  rules,
  count,
  disabled,
  onAsk,
}: {
  rules: CrawlDeepRules | undefined;
  count: number;
  disabled: boolean;
  onAsk: () => void;
}) {
  if (rules === undefined) return null;
  return (
    <Button
      variant="light"
      className="press"
      disabled={disabled}
      aria-haspopup="dialog"
      onClick={onAsk}
    >
      {deepLabel(rules)} отмеченных · {formatNumber(count)}
    </Button>
  );
}

export function DeepConfirm({
  opened,
  rules,
  who,
  count,
  loading,
  onClose,
  onConfirm,
}: {
  opened: boolean;
  rules: CrawlDeepRules;
  /** Кого обходим — словами, первой строкой окна. */
  who: ReactNode;
  count: number;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const now = deepNow(rules);
  return (
    <Modal opened={opened} onClose={onClose} title={deepTitle(rules)}>
      <Stack gap="sm">
        <Text>{who}</Text>
        <Text size="sm" c="dimmed">
          {deepTiming(count, rules)}
        </Text>
        {now !== null ? (
          <Text size="sm" c="dimmed">
            {now}
          </Text>
        ) : null}
        <Text size="sm" c="dimmed">
          {deepCost(rules)}
        </Text>
        <Group justify="flex-end" gap="xs">
          <Button variant="default" className="press" onClick={onClose}>
            Отмена
          </Button>
          <Button className="press" loading={loading} onClick={onConfirm}>
            Обойти заново
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Что такое «Заново» — числами сервера; сервер старше экрана — без неё. */
export function deepRule(deep: CrawlDeepRules | undefined): string {
  if (deep === undefined) return '';
  return (
    ` «${deepLabel(deep)}» обходит донора с начала с потолком в ${formatNumber(deep.max_pages)} ` +
    `страниц: такой обход идёт часами, разом их не больше ${formatNumber(deep.max_running)}, ` +
    'а остальные обходы идут мимо них.'
  );
}

/** «2 донора отмечено» или имя одного — первой строкой окна «Обойти заново». */
export function deepWho(hosts: string[]): string {
  if (hosts.length === 1) return `Донор ${hosts[0]}.`;
  const count = formatNumber(hosts.length);
  return `Отмечено ${count} ${plural(hosts.length, 'донор', 'донора', 'доноров')}.`;
}

/** Окно «Обойти заново» — одно на кнопку в строке и на отмеченных. */
export function DeepModal({
  hosts,
  rules,
  onClose,
  onDone,
}: {
  hosts: string[];
  rules: CrawlDeepRules | undefined;
  onClose: () => void;
  onDone: () => void;
}) {
  const start = useStartDeep(onDone);
  if (rules === undefined) return null;
  return (
    <DeepConfirm
      opened={hosts.length > 0}
      rules={rules}
      who={deepWho(hosts)}
      count={hosts.length}
      loading={start.isPending}
      onClose={onClose}
      onConfirm={() => start.mutate(hosts)}
    />
  );
}
