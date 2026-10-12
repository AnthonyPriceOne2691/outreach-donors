/**
 * Журнал действий: кто, что и когда сделал в сервисе — постранично, с фильтрами по
 * действию, автору и дням (ТЗ, вариант 3: «роли, журнал действий»; сверка ТЗ 11.10.2026).
 *
 * Записи пишутся с первого дня — той же транзакцией, что и само действие, — а прочитать
 * их было негде. Экран — рядом с «Учётками» и под тем же правом (`users`): в журнале
 * входы и отказы входа с почтами, выданные права, сброшенные пароли — это разбор
 * доступа, а не работа с базой.
 *
 * **Фильтры и страница — в адресе** (`journalFilters.ts`): ссылку на «входы Ивана за
 * вторник» отправляют коллеге. Смена фильтра — не перезагрузка: прежние строки стоят
 * приглушёнными, пока едет ответ (`keepPreviousData`). Отрезок, у которого конец раньше
 * начала, на сервер не уходит: под полем — почему.
 *
 * **Без права «Продажи» — без действий продаж**, и экран говорит это строкой: иначе
 * журнал без них читался бы как весь (П2).
 */

import { Alert, Card, Group, Loader, Select, Stack, Text, TextInput } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

import { actionFilterTitle, AUDIT_ACTIONS, SALES_ACTIONS } from '../api/auditLabels';
import { refusalOf } from '../api/client';
import { fetchJournal } from '../api/journal';
import type { AuditAction, JournalView } from '../api/journal';
import type { UserCard } from '../api/types';
import { listUsers } from '../api/users';
import { useSession } from '../auth/AuthProvider';
import { PageHead } from '../components/PageHead';
import { PageSwitch } from '../components/PageSwitch';
import { formatNumber, plural } from '../format';
import { JournalTable } from './JournalTable';
import type { JournalFilters } from './journalFilters';
import { journalParams, momentsOf, rangeProblem, readJournalFilters } from './journalFilters';

const HINT =
  'Сюда пишется вход и всё, что меняет состояние или тратит деньги: запуски, письма, решения, правки доступа. Чтение не пишется — оно утопило бы журнал. Запись ложится той же транзакцией, что и само действие, и не правится.';

/** Ширины полей — по самому длинному значению: подпись фильтра «Правка — доступ…»,
 *  почта учётки, дата. На телефоне поле — во всю ширину. */
const WIDTH = { action: 360, author: 260, day: 170 } as const;

const ACTIONS = Object.keys(AUDIT_ACTIONS) as AuditAction[];

interface FiltersProps {
  filters: JournalFilters;
  problem: string | null;
  users: UserCard[];
  /** Без права «Продажи» действий продаж нет и в списке фильтра. */
  withSales: boolean;
  onChange: (patch: Partial<JournalFilters>, replace?: boolean) => void;
}

/** Фильтры над журналом: действие, автор, первый и последний день. */
function Filters({ filters, problem, users, withSales, onChange }: FiltersProps) {
  const actions = ACTIONS.filter((action) => withSales || !SALES_ACTIONS.has(action));
  return (
    <Group align="flex-start" gap="md" className="fieldRow runFields">
      <Select
        label="Действие"
        description="Пусто — все"
        placeholder="все действия"
        clearable
        searchable
        value={filters.action}
        data={actions.map((action) => ({ value: action, label: actionFilterTitle(action) }))}
        onChange={(value) => onChange({ action: (value as AuditAction | null) ?? null })}
        comboboxProps={{ width: 'target', position: 'bottom-start' }}
        w={{ base: '100%', xs: WIDTH.action }}
      />
      <Select
        label="Кто"
        description="Пусто — все"
        placeholder="все учётки"
        clearable
        searchable
        value={filters.author === null ? null : String(filters.author)}
        data={users.map((user) => ({ value: String(user.id), label: user.email }))}
        onChange={(value) => onChange({ author: value === null ? null : Number(value) })}
        comboboxProps={{ width: 'target', position: 'bottom-start' }}
        w={{ base: '100%', xs: WIDTH.author }}
      />
      <TextInput
        type="date"
        label="С"
        description="с начала дня"
        value={filters.from}
        onChange={(event) => onChange({ from: event.currentTarget.value }, true)}
        w={{ base: '100%', xs: WIDTH.day }}
      />
      <TextInput
        type="date"
        label="По"
        description="включительно"
        value={filters.to}
        error={problem}
        onChange={(event) => onChange({ to: event.currentTarget.value }, true)}
        w={{ base: '100%', xs: WIDTH.day }}
      />
    </Group>
  );
}

/** Сколько записей — и что значит ноль: «под фильтр ничего не попало» и «журнал пуст»
 *  решаются по-разному. */
function countLine(view: JournalView, filtered: boolean): string {
  if (view.total === 0) {
    return filtered
      ? 'Под фильтр ничего не попало — снимите фильтр или расширьте даты.'
      : 'Записей пока нет.';
  }
  const records = plural(view.total, 'запись', 'записи', 'записей');
  return `${formatNumber(view.total)} ${records}${filtered ? ' под фильтром' : ''}, новые сверху`;
}

interface ResultsProps {
  view: JournalView;
  /** Новый ответ не пришёл — прежние строки стоят, отказ — над ними. */
  error: Error | null;
  filtered: boolean;
  stale: boolean;
  page: number;
  pages: number;
  emails: ReadonlyMap<number, string>;
  onPage: (page: number) => void;
}

function Results({ view, error, filtered, stale, page, pages, emails, onPage }: ResultsProps) {
  return (
    <>
      {error !== null && (
        <Alert color="red" title="Журнал не обновился" m="md">
          {refusalOf(error)}
        </Alert>
      )}
      <Text size="sm" c="dimmed" px="md" pt="xs" pb="sm">
        {countLine(view, filtered)}
      </Text>
      {view.entries.length > 0 && (
        <JournalTable entries={view.entries} emails={emails} stale={stale} />
      )}
      <PageSwitch
        label="Страницы журнала"
        page={page}
        pages={pages}
        onChange={onPage}
        pt="xs"
        pb="sm"
      />
    </>
  );
}

/** Ответа нет: отрезок с концом раньше начала — словами; отказ — целиком; иначе крутилка. */
function Waiting({ problem, error }: { problem: string | null; error: Error | null }) {
  if (problem !== null) {
    return (
      <Text size="sm" c="dimmed" p="md">
        Конец отрезка раньше начала — такой отрезок пуст. Поправьте даты, и журнал покажет записи.
      </Text>
    );
  }
  if (error !== null) {
    return (
      <Alert color="red" title="Журнал не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  return <Loader aria-label="Загружаем журнал" m="md" />;
}

export function JournalPage() {
  const { can } = useSession();
  const [params, setParams] = useSearchParams();
  const filters = readJournalFilters(params);
  const problem = rangeProblem(filters.from, filters.to);
  const { since, until } = momentsOf(filters.from, filters.to);

  // Тот же ключ, что у «Учёток»: список учёток один, и в кэше он один.
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers });
  const journal = useQuery({
    queryKey: ['journal', filters.page, filters.action, filters.author, since, until],
    queryFn: () => fetchJournal({ ...filters, since, until }),
    enabled: problem === null,
    placeholderData: keepPreviousData,
  });

  const view = problem === null ? (journal.data ?? null) : null;
  const pages = view === null ? 1 : Math.max(1, Math.ceil(view.total / view.limit));
  const settled = view !== null && !journal.isPlaceholderData;
  const change = (patch: Partial<JournalFilters>, replace = false) =>
    setParams(journalParams({ ...filters, page: 1, ...patch }), { replace });

  // Страница за концом — ссылка, открытая, когда записей под фильтром стало меньше:
  // экран уходит на последнюю, заменяя адрес, а не добавляя.
  useEffect(() => {
    if (settled && filters.page > pages) {
      setParams(journalParams({ ...filters, page: pages }), { replace: true });
    }
  }, [settled, filters, pages, setParams]);

  const userList = users.data ?? [];
  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <PageHead title="Журнал действий" hint={HINT} />
          <Filters
            filters={filters}
            problem={problem}
            users={userList}
            withSales={can('sales')}
            onChange={change}
          />
          {view?.sales_hidden === true && (
            <Text size="sm" c="dimmed">
              Действия продаж не показаны — их видно с правом «Продажи». Журнал ниже — без них: это
              не все записи.
            </Text>
          )}
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что у шапки. */}
      <Card className="glass" p="md">
        {view === null ? (
          <Waiting problem={problem} error={journal.error} />
        ) : (
          <Results
            view={view}
            error={journal.error}
            filtered={[filters.action, filters.author, since, until].some((one) => one !== null)}
            stale={journal.isPlaceholderData}
            page={filters.page}
            pages={pages}
            emails={new Map(userList.map((user) => [user.id, user.email]))}
            onPage={(next) => setParams(journalParams({ ...filters, page: next }))}
          />
        )}
      </Card>
    </Stack>
  );
}
