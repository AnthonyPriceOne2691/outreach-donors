/**
 * Таблица доноров с фильтрами в шапке — каждый под своей колонкой.
 *
 * **Фильтр живёт у колонки, которую сужает.** Замечание 25.09.2026: блок
 * фильтров над таблицей убрать, «функционал размазать по фильтрам в
 * таблице». Поиск стоит под «Донором», вердикт — под «Вердиктом», порог DR —
 * под «DR», адрес — под «Адресами»: что сужает колонку, видно, не читая
 * подписей. С 26.09.2026 фильтр есть под каждой колонкой: трафик «не ниже»,
 * страна со счётчиками и поиском по названию, срок метрик.
 *
 * **Отметка — первой колонкой** (замечание 26.09.2026: «заведи возможность
 * отмечать, какие именно доноры можно выгрузить»). Флажок в шапке — все
 * на этой странице; отмечена часть — промежуточное состояние. Имя донора
 * при этом по-прежнему читается слева (`dataTable pickFirst`).
 *
 * **Ширины заданы, раскладка фиксированная.** При автоматической колонки
 * подстраиваются под содержимое страницы, и таблица «прыгает» от страницы
 * к странице и от фильтра к фильтру. Ширины — по самому длинному, что
 * в колонке бывает, включая то, чего нет в сегодняшних данных, и замерены
 * шрифтом экрана: значок, ужатый до многоточия, не значит ничего (урок
 * 21.09.2026), а обрезанное значение фильтра — не то значение.
 *
 * **На телефоне строка фильтров уезжает в прокрутку вместе с таблицей.**
 * Фильтр остаётся под своей колонкой, а не переезжает в отдельную стопку
 * над таблицей: вторая раскладка — второй экран для проверки, и связь
 * «фильтр — колонка» на ней теряется. Поиск по домену и отметка — в первых
 * колонках и видны без прокрутки; к остальным фильтрам ведёт та же
 * прокрутка, что и к их колонкам.
 *
 * **Цена — строкой под доменом** (сверка ТЗ 12.10.2026: «таблица проверенных доноров
 * с ценами»): сумма с валютой и днём, когда её назвали, или «устарела», если старше
 * срока; цену снял ответ донора — его словами. Не своей колонкой: на 1440 с раскрытым
 * меню колонкам остаётся 12 px сверх ширин, замеренных по самому длинному содержимому,
 * а цене с полем фильтра нужно 104 — таблица уехала бы в прокрутку вбок. Фильтр цены,
 * решение человека и порядок — над таблицей (`DonorsScope.tsx`).
 */

import { Anchor, Badge, Checkbox, Group, Table, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import {
  CONTACT_STATUSES,
  countryTitle,
  DONOR_FRESHNESS,
  DONOR_STATUSES,
  NOT_SEARCHED,
  SELLER_ANSWERS,
} from '../api/labels';
import type { DonorRowCard } from '../api/types';
import { ColumnsHead } from '../components/ColumnsHead';
import type { Column } from '../components/ColumnsHead';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { NoRows } from '../components/WholeRow';
import { formatCompact, formatDate, formatMoney, formatNumber, formatShare } from '../format';
import type { Emptiness } from './donorFilters';
import { FilterRow } from './DonorsFilterRow';
import type { FilterRowProps } from './DonorsFilterRow';
import type { Picks } from './picks';
import { DONOR_REVIEWS } from './priceWords';
import { placeOf } from './place';
import type { DonorPlace } from './place';

/** Колонки слева направо. Ширина донора — остаток: в ней домен, и ей
 *  отдаётся всё, что не нужно остальным. Остальные — по самому длинному,
 *  что в колонке бывает, замеренному шрифтом экрана (холстом по живому
 *  элементу, 26.09.2026), плюс поля ячейки, поля и стрелки списка:
 *  - вердикт — значок и поле «не проверен» без числа (09.10.2026);
 *  - DR — подсказка «не ниже» (48);
 *  - трафик — порог «1 000 000 000» (84): миллиарды бывают, у крупнейшего
 *    сайта базы пять;
 *  - гео — строка «Саудовская Аравия · 62%» (173); в поле — только название,
 *    счётчик — в списке: по названию в поле ищут;
 *  - адреса — число и значок «предел запросов» (140);
 *  - данные — значок и поле «не проверялись» без числа.
 *
 *  **Счётчики — только в пунктах списка, в поле — название**, как у «Гео». До
 *  09.10.2026 поле показывало «не проверялись · 1 225», и колонки «Вердикт» и
 *  «Данные» держали 176 и 196 px ради закрытого поля, а в ячейках — короткий
 *  значок. Домену оставалось ~170 px из 1 161: домены переносились, один — посреди
 *  слова, строки шли разной высоты (аудит экранов 09.10.2026).
 *
 *  **Поле ячейки — 8 px, а не 10** (проверка прода 10.10.2026): на 1440 с раскрытым
 *  меню домену оставалось 164 px текста, и слитный домен в 23 знака — шва в нём нет —
 *  рвался посреди слова. Ширины остальных колонок ужаты ровно на 4 px — на сколько
 *  выросло место под содержимое, — а 28 px ушли домену: 196 px текста, ~24 знака. */
const COLUMNS: Column[] = [
  { title: 'Отметка', width: '2.25rem' },
  { title: 'Донор' },
  { title: 'Вердикт', width: '8.5rem', hint: <VerdictHint /> },
  { title: 'DR', width: '5.75rem' },
  { title: 'Трафик', width: '7.75rem' },
  { title: 'Гео', width: '12rem' },
  { title: 'Адреса', width: '9.75rem' },
  { title: 'Данные', width: '9.5rem' },
];

/** Поле ячейки слева и справа, px (см. выше). */
const CELL_GAP = 8;

/** Уже этого таблица не сжимается и уезжает в прокрутку: остальным колонкам —
 *  их ширины (888 px), домену — 168. Поле поиска под ним — 152 px, и подсказка
 *  «Домен или причина» (139 px с полями) встаёт целиком: при 160 px колонки и
 *  полях 10 px она резалась до «Домен или причин» на 1280 и телефоне (проверка
 *  прода 10.10.2026). Шире не надо: на телефоне за доменом виден значок вердикта.
 *  На 1440 таблица шире (1 100) — прокрутки нет; на 1280 и уже — есть. */
export const TABLE_MIN_WIDTH = 1056;

/** «Не проверен» — не «не подходит». Пояснение не теряется вместе с блоком,
 *  где оно стояло: оно у заголовка колонки, прямо над фильтром вердикта. */
function VerdictHint() {
  return (
    <InfoHint name="Что значит «не проверен»" width={280}>
      «Не проверен» — не «не подходит»: у домена не было данных, и его надо добрать позже, а не
      закрыть. Причина отсева — под доменом.
    </InfoHint>
  );
}

/** Цена строки — под доменом: сумма с валютой и днём, когда её назвали, или
 *  «устарела», если старше срока (сначала перезапрос). Цену снял ответ донора — его
 *  словами, как в карточке: пустое место читалось бы «не спрашивали». Без цены — ничего. */
function PriceLine({ donor }: { donor: DonorRowCard }) {
  const answer = donor.seller_answer ?? null;
  if (donor.last_price === null) {
    if (answer !== 'declines' && answer !== 'free') return null;
    return (
      <Text size="xs" c="dimmed">
        {SELLER_ANSWERS[answer].title}
      </Text>
    );
  }
  const money = formatMoney(donor.last_price, donor.last_price_currency);
  return (
    <Group gap={6} wrap="nowrap" className="donorPrice">
      <Text size="xs" c="var(--ink)" fw={500}>
        {money}
      </Text>
      {donor.price_stale === true ? (
        <Badge variant="light" color="yellow" size="sm">
          устарела
        </Badge>
      ) : (
        <Text size="xs" c="dimmed">
          {formatDate(donor.last_price_at)}
        </Text>
      )}
    </Group>
  );
}

/** Решение человека под доменом — у записи, которая не донор: экран показывает
 *  её, только когда выбрано другое решение (фильтр «Решение»). */
function NotADonor({ donor }: { donor: DonorRowCard }) {
  if (donor.review === 'accepted' || donor.review === undefined) return null;
  const decision = DONOR_REVIEWS[donor.review ?? 'undecided'];
  return (
    <Text size="xs" c="dimmed">
      не донор: {decision.title}
    </Text>
  );
}

function AddressCell({ donor }: { donor: DonorRowCard }) {
  const state =
    donor.contact_status === null ? NOT_SEARCHED : CONTACT_STATUSES[donor.contact_status];
  return (
    // Число и значок — одной группой постоянной ширины: числа встают
    // столбиком, значки начинаются с одной линии, а группа — по центру.
    <span className="addressCell">
      <span className="addressCount">{formatNumber(donor.contacts)}</span>
      <Badge variant="light" size="sm" color={state.color}>
        {state.title}
      </Badge>
    </span>
  );
}

interface RowProps {
  donor: DonorRowCard;
  place: DonorPlace;
  picked: boolean;
  onPick: (id: number) => void;
  onOpen: (donor: DonorRowCard) => void;
}

function DonorRow({ donor, place, picked, onPick, onOpen }: RowProps) {
  // Слово считает сервер тем же условием, что фильтр «Данные»: данных
  // не было — «не проверялись», а не «пора обновить» — обновлять нечего.
  const fresh = DONOR_FRESHNESS[donor.freshness];
  return (
    <Table.Tr style={{ cursor: 'pointer' }} onClick={() => onOpen(donor)}>
      {/* Щелчок по ячейке отметки не открывает карточку: промахнуться мимо
          флажка на пару пикселей — не повод уходить со страницы. */}
      <Table.Td onClick={(event) => event.stopPropagation()}>
        <Checkbox
          size="sm"
          aria-label={`Отметить ${donor.host}`}
          checked={picked}
          onChange={() => onPick(donor.id)}
        />
      </Table.Td>
      <Table.Td>
        {/* Ссылка, а не только строка: с клавиатуры до строки не дойти,
            а карточку донора открывают и в новой вкладке. Цвет — чернила,
            а не акцент: щёлкают строку целиком, её подсветка и есть отклик,
            а бирюзовая ссылка в верхних строках стоит на бирюзовом углу
            полотна — в тёмной теме это 4,3–4,9 : 1 в зависимости от того,
            где сейчас плывёт пятно (замер 25.09.2026). */}
        <Anchor
          component={Link}
          to={{ pathname: `/donors/${donor.id}` }}
          state={place}
          fw={500}
          c="var(--ink)"
          underline="hover"
          className="donorHost"
          onClick={(event) => event.stopPropagation()}
        >
          {/* Перенос — по швам домена, а не где кончилось место:
              «analysis.example.te / st» (06.10.2026). */}
          <Seams text={donor.host} />
        </Anchor>
        {donor.reject_reason !== null && (
          <Text size="xs" c="dimmed">
            {donor.reject_reason}
          </Text>
        )}
        <NotADonor donor={donor} />
        <PriceLine donor={donor} />
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <Badge variant="light" color={DONOR_STATUSES[donor.status].color}>
            {DONOR_STATUSES[donor.status].title}
          </Badge>
        </Group>
      </Table.Td>
      <Table.Td>{formatNumber(donor.dr)}</Table.Td>
      <Table.Td>{formatCompact(donor.org_traffic)}</Table.Td>
      <Table.Td>
        {donor.geo === null
          ? '—'
          : `${countryTitle(donor.geo)}${
              donor.geo_top_share === null ? '' : ` · ${formatShare(donor.geo_top_share)}`
            }`}
      </Table.Td>
      <Table.Td>
        <AddressCell donor={donor} />
      </Table.Td>
      <Table.Td>
        {/* Свежесть — это про деньги: за свежие данные второй раз
            не платят, поэтому она видна в таблице, а не в карточке. */}
        <Group justify="center">
          <Badge variant="light" color={fresh.color}>
            {fresh.title}
          </Badge>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

/** Флажок «все на этой странице»: отмечена часть — промежуточное состояние. */
function PickAll({ rows, picks }: { rows: DonorRowCard[]; picks: Picks }) {
  const ids = rows.map((donor) => donor.id);
  const on = ids.filter((id) => picks.picked.has(id)).length;
  const all = ids.length > 0 && on === ids.length;
  return (
    <Checkbox
      size="sm"
      aria-label="Отметить всех на этой странице"
      checked={all}
      indeterminate={on > 0 && !all}
      disabled={ids.length === 0}
      onChange={() => picks.setAll(ids, !all)}
    />
  );
}

interface Props extends FilterRowProps {
  rows: DonorRowCard[];
  /** Идёт запрос новой страницы — строки старые, и это видно. */
  stale: boolean;
  empty: Emptiness | null;
  refusal: string | null;
  /** Откуда пришли в карточку: адрес списка с фильтрами — для «назад». */
  from: string;
  picks: Picks;
  onOpen: (donor: DonorRowCard) => void;
  onReset: () => void;
}

export function DonorsTable({
  rows,
  stale,
  empty,
  refusal,
  from,
  picks,
  onOpen,
  onReset,
  ...filters
}: Props) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable pickFirst filteredTable donorsTable"
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
        horizontalSpacing={CELL_GAP}
      >
        <ColumnsHead
          columns={COLUMNS}
          heads={{ Отметка: <PickAll rows={stale ? [] : rows} picks={picks} /> }}
        >
          <FilterRow {...filters} />
        </ColumnsHead>
        <Table.Tbody className="staleRows" data-stale={stale || undefined}>
          {/* Отказ или «пусто и почему» — строкой на всю ширину: фильтры над ней
              остаются, поправить условие можно тут же, не возвращаясь. */}
          <NoRows
            span={COLUMNS.length}
            refusal={refusal}
            failTitle="Доноры не загрузились"
            empty={empty}
            onReset={onReset}
          />
          {refusal === null &&
            rows.map((donor) => (
              <DonorRow
                key={donor.id}
                donor={donor}
                place={placeOf(from, rows)}
                picked={picks.picked.has(donor.id)}
                onPick={picks.toggle}
                onOpen={onOpen}
              />
            ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
