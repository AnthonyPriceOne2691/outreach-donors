/**
 * Две ручные очереди «Форм» — доноры и рекламодатели — одним описанием на очередь:
 * откуда строки, что уходит на сервер, колонки и слова. Таблица, окно адреса,
 * подтверждение «Не вышло» и ориентир темпа у них общие (`FormsQueue`): копии разошлись
 * бы при первой правке, как разошлись три копии переключателя страниц.
 *
 * **Рекламодатели — сверка ТЗ 11.10.2026**: строка «только форма, нет email —
 * заполняем вручную» общая на оба этапа, а очередь была только у доноров. Вместо DR
 * и трафика у рекламодателя — донор, на котором нашли ссылку (или «бизнес ниши»),
 * и балл: метрик у него нет.
 */

import { Badge, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import {
  advertiserFormFilled,
  advertiserFormGaveUp,
  fetchAdvertiserForms,
  fetchForms,
  formFilled,
  formGaveUp,
} from '../api/contacts';
import type { AdvertiserFormCard, AdvertiserFormsView } from '../api/contacts';
import type { FormCard, FormsView } from '../api/types';
import { Seams } from '../components/Seams';
import { formatCompact, formatDate, formatNumber, plural } from '../format';

export type FormsTab = 'donors' | 'advertisers';

/** Страница очереди, как её отдаёт сервер: строки, всего, размер и ориентир темпа. */
export interface QueuePage<Row> {
  rows: Row[];
  total: number;
  page: number;
  limit: number;
  monthly_left: number;
  monthly_cap: number;
}

export interface QueueColumn<Row> {
  title: string;
  /** Доля ширины таблицы — см. `FormsQueue`. */
  share: number;
  cell: (row: Row) => ReactNode;
}

export interface FormQueue<Row, Page extends QueuePage<Row> = QueuePage<Row>> {
  tab: FormsTab;
  queryKey: readonly string[];
  fetch: (page: number) => Promise<Page>;
  fill: (row: Row, email: string) => Promise<Row>;
  giveUp: (row: Row) => Promise<Row>;
  idOf: (row: Row) => number;
  hostOf: (row: Row) => string;
  /** Первая колонка — имя сайта. */
  name: { title: string; share: number };
  columns: QueueColumn<Row>[];
  /** Сколько ждёт во второй вкладке — из этого же ответа; нет — сервер старше экрана. */
  other: (page: Page) => number | undefined;
  hint: string;
  empty: string;
  /** «донора» / «рекламодателя» — в заголовке окна адреса. */
  whose: string;
  /** Что станет с адресом — над полем окна. */
  about: string;
  filled: (host: string) => string;
  /** Подтверждение «Не вышло» — тем, что сделает код: адрес поищут снова по сроку. */
  closeAsk: (host: string, page: Page) => string;
}

/** Когда «Не вышло» вернёт сайт в поиск: срок годности от прошлого поиска (`forms.gave_up`,
 *  `CONTACT_TTL_DAYS`). Число — от сервера; сервер старше экрана — словами без числа. */
function again(days: number | undefined): string {
  if (days === undefined || days <= 0) return 'по сроку годности прошлого поиска';
  return `через ${formatNumber(days)} ${plural(days, 'день', 'дня', 'дней')} после прошлого поиска`;
}

/** «Искали» — когда лестница дошла до формы; одна колонка у обеих очередей. */
function searched<Row extends { attempted_at: string | null }>(): QueueColumn<Row> {
  return { title: 'Искали', share: 17, cell: (row) => formatDate(row.attempted_at) };
}

export const DONOR_FORMS: FormQueue<FormCard, FormsView> = {
  tab: 'donors',
  queryKey: ['forms'],
  fetch: fetchForms,
  fill: (row, email) => formFilled(row.donor_id, email),
  giveUp: (row) => formGaveUp(row.donor_id, null),
  idOf: (row) => row.donor_id,
  hostOf: (row) => row.host,
  name: { title: 'Донор', share: 22 },
  columns: [
    {
      title: 'DR',
      share: 15,
      cell: (row) => <Badge variant="light">{row.dr ?? '—'}</Badge>,
    },
    { title: 'Трафик', share: 17, cell: (row) => formatCompact(row.org_traffic) },
    searched(),
  ],
  other: (page) => page.advertisers_waiting,
  hint:
    'У этих доноров есть контактная форма и нет почты. Лестница дошла до них и остановилась: ' +
    'дальше нужны руки. Заполнили и получили адрес — впишите его, и донор пойдёт в очередь ' +
    'писем обычным порядком.',
  empty: 'Очередь пуста. Сюда попадают доноры, у которых лестница нашла форму, но не нашла адреса.',
  whose: 'донора',
  about:
    'Адрес, который донор дал в ответ на форму. Он попадёт в базу как введённый руками — ' +
    'в журнале останется, кто его внёс.',
  filled: (host) => `${host}: адрес записан, донор пойдёт в очередь писем`,
  closeAsk: (host, page) =>
    `Закрыть ${host} без адреса? Донор уйдёт из очереди форм; адрес ему поищут снова ` +
    `${again(page.retry_days)} — до этого письма не будет.`,
};

/** Откуда рекламодатель: донор, на котором нашли ссылку, — или бизнес ниши с прогоном. */
function whereFrom(row: AdvertiserFormCard): ReactNode {
  if (row.source === 'niche') {
    return row.run_id === null ? 'бизнес ниши' : `бизнес ниши, прогон №${row.run_id}`;
  }
  return row.donor_host === null ? '—' : <Seams text={row.donor_host} />;
}

export const ADVERTISER_FORMS: FormQueue<AdvertiserFormCard, AdvertiserFormsView> = {
  tab: 'advertisers',
  queryKey: ['forms', 'advertisers'],
  fetch: fetchAdvertiserForms,
  fill: (row, email) => advertiserFormFilled(row.advertiser_id, email),
  giveUp: (row) => advertiserFormGaveUp(row.advertiser_id, null),
  idOf: (row) => row.advertiser_id,
  hostOf: (row) => row.host,
  name: { title: 'Рекламодатель', share: 22 },
  columns: [
    {
      title: 'Донор',
      share: 20,
      cell: (row) => (
        <Text size="sm" className="cellName">
          {whereFrom(row)}
        </Text>
      ),
    },
    {
      title: 'Балл',
      share: 12,
      cell: (row) => (row.source === 'niche' ? '—' : <Badge variant="light">{row.points}</Badge>),
    },
    searched(),
  ],
  other: (page) => page.donors_waiting,
  hint:
    'У этих рекламодателей есть контактная форма и нет почты: пишем им, а лестница дошла до ' +
    'формы и остановилась. Заполнили и получили адрес — впишите его, и рекламодателю уйдёт ' +
    'оффер обычным порядком.',
  empty:
    'Очередь пуста. Сюда попадают рекламодатели, которым пишем, — у них лестница нашла форму, ' +
    'но не нашла адреса.',
  whose: 'рекламодателя',
  about:
    'Адрес, который рекламодатель дал в ответ на форму. Он попадёт в базу как введённый ' +
    'руками — в журнале останется, кто его внёс, — и оффер уйдёт на него.',
  filled: (host) => `${host}: адрес записан, ему уйдёт оффер`,
  closeAsk: (host, page) =>
    `Закрыть ${host} без адреса? Рекламодатель уйдёт из очереди форм; адрес ему поищут снова ` +
    `${again(page.retry_days)} — до этого оффера не будет.`,
};
