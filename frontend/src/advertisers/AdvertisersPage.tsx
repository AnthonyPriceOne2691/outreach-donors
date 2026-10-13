/**
 * Ручная проверка кандидатов в рекламодатели.
 *
 * **Экран существует ради одного числа.** Требование ограничивает долю
 * ложных рекламодателей десятью процентами, а скоринг судит по пяти
 * признакам, два из которых выведены из замера на одной нише. Без
 * человека, который смотрит пограничные, эта доля не измерена
 * и не удержана.
 *
 * **Причины показываются целиком, а не прячутся за баллом.** Человек
 * решает не по числу «3», а по тому, из чего оно сложилось: «ссылки
 * с двадцати страниц донора» и «коммерческий анкор под nofollow» — это
 * разные основания, и одно из них бывает ошибкой.
 *
 * **Страница и анкор — рядом с решением.** Письмо пишется под конкретную
 * найденную ссылку, и человек должен видеть ту самую, по которой оно
 * будет написано, а не догадываться о ней.
 *
 * **Счётчики по всем вердиктам, включая отсеянных.** По ним видно, что
 * список «кому не пишем» работает, а не молчит.
 *
 * **Числа и текст сходятся.** «Спорно: 1» над «Спорных нет» читалось как
 * сбой: спорный был, но решённый, и прятался за выключенным «Показывать
 * решённые» без объяснения (аудит 25.09.2026). Пустая таблица теперь
 * говорит, сколько решено и где их видно.
 *
 * **«Куплено» — тоже списком** (06.10.2026). Раньше это было число в шапке,
 * и ложного среди купленных снять было нечем, кроме консоли; письмо уходит
 * именно им. Сверху — обход доноров (`CrawlPanel`): откуда кандидаты берутся.
 * Под очередью — бизнесы ниши из выдачи (`NicheQueue`), последним — «К письму»
 * (`PromotePanel`): перевод в рекламодатели и их адреса. Порядок блоков — порядок
 * работы (слово владельца 13.10.2026): перевод и поиск адресов — после всех решений выше.
 *
 * **Доля ложных — в шапке очереди, с допуском ТЗ** (`FalseShare`, сверка ТЗ
 * 11.10.2026): ради неё экран и сделан, а до этого её не было нигде.
 *
 * **Три вида — переключателем вверху** (`AdvertiserViews`): проверка (всё выше,
 * таблица очереди — `CandidatesTable`), все рекламодатели списком с выгрузкой
 * (`AdvertiserList`) и приёмка по донорам (`AcceptanceReport`).
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  SegmentedControl,
  Stack,
  Switch,
  Text,
} from '@mantine/core';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { ReviewedVerdict } from '../api/advertisers';
import { decideCandidate, fetchCandidates } from '../api/advertisers';
import type { CandidateCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { PageHead } from '../components/PageHead';
import { AcceptanceReport } from './AcceptanceReport';
import { AdvertiserList } from './AdvertiserList';
import { useAdvertiserView, ViewSwitch } from './AdvertiserViews';
import { CandidatesTable, VERDICTS } from './CandidatesTable';
import { CrawlPanel } from './CrawlPanel';
import { FalseShare } from './FalseShare';
import { NicheQueue } from './NicheQueue';
import { PromotePanel } from './PromotePanel';
import { notify } from '../notices';

const QUERY_KEY = ['advertisers'] as const;

/** Что смотрит человек — спорных или «куплено» — и что это значит. */
const LISTS: Record<ReviewedVerdict, { title: string; lead: string }> = {
  pending: {
    title: 'Рекламодатели: спорные',
    lead:
      'Скоринг разложил найденные ссылки на три кучи: куплена, мимо и спорно. Спорные — здесь. ' +
      'Решение человека сильнее вердикта скоринга и балл не переписывает: по расхождению между ' +
      'ними и видно, как часто скоринг ошибается.',
  },
  bought: {
    title: 'Рекламодатели: куплено',
    lead:
      'Ссылки, которые скоринг счёл купленными: им уйдёт оффер. Ложного снимите «Не пишем» ' +
      'до письма — балл при этом не переписывается.',
  },
};

/** Почему таблица пуста — словами, и с теми же числами, что в шапке. */
function EmptyQueue({
  verdict,
  decided,
  onShowDecided,
}: {
  verdict: ReviewedVerdict;
  decided: number;
  onShowDecided: (() => void) | null;
}) {
  if (verdict === 'bought') {
    return (
      <Text size="sm" c="dimmed">
        Купленных нет. Сюда попадают ссылки с пометкой рекламы или с четырьмя баллами и больше — им
        и уходит оффер. Решённые «не пишем» скрыты, пока выключено «Показывать решённые».
      </Text>
    );
  }
  if (decided > 0 && onShowDecided !== null) {
    return (
      <Stack gap={6} align="flex-start">
        <Text size="sm" fw={500}>
          Все спорные решены — {formatNumber(decided)}.
        </Text>
        <Text size="sm" c="dimmed">
          Решённые скрыты, пока выключено «Показывать решённые»: по ним уже сказали «пишем» или «не
          пишем».
        </Text>
        <Button variant="subtle" size="compact-sm" className="press" onClick={onShowDecided}>
          Показать решённые
        </Button>
      </Stack>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      Спорных нет. Сюда попадают ссылки, набравшие 2–3 балла: одного признака хватило, чтобы
      заподозрить размещение, и не хватило, чтобы решить за человека.
    </Text>
  );
}

/** Вид «Проверка»: обход доноров, спорные и «куплено», перевод к письму, бизнесы ниши. */
function ReviewView() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [includeDecided, setIncludeDecided] = useState(false);
  const [verdict, setVerdict] = useState<ReviewedVerdict>('pending');

  const query = useQuery({
    queryKey: [...QUERY_KEY, includeDecided, verdict],
    queryFn: () => fetchCandidates(includeDecided, verdict),
    // Переключатель не перерисовывает экран: прежняя таблица стоит
    // приглушённой, пока не придёт новая. Раньше экран целиком сменялся
    // значком загрузки — «как будто страница загружается заново».
    placeholderData: keepPreviousData,
  });
  const { data, isLoading, error } = query;
  const stale = query.isPlaceholderData;

  const decide = useMutation({
    mutationFn: ({ row, confirmed }: { row: CandidateCard; confirmed: boolean }) =>
      decideCandidate(row.id, confirmed, row.confirmed !== null),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      notify({
        message: row.confirmed ? `${row.target_root}: пишем ему` : `${row.target_root}: не пишем`,
        color: 'green',
      });
      // Рекламодатель уже ответил: «не пишем» сняло оффер и добивки, а переписку — нет.
      // Жёлтым и не гаснет: это просьба посмотреть «Диалоги», а не отчёт.
      if (row.warning) notify({ message: row.warning, color: 'yellow', autoClose: false });
    },
    onError: (failure) =>
      notify({ title: 'Не записали', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем очередь проверки" m="md" />;
  if (error && data === undefined) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const rows = data?.rows ?? [];
  const counts = data?.counts ?? {};
  const waiting = data?.waiting ?? 0;
  // Спорные, по которым человек уже решил: счётчик «спорно» считает всех,
  // «ждут человека» — только нерешённых.
  const decided = Math.max(0, (counts['pending'] ?? 0) - waiting);
  const mayDecide = can('prices');

  return (
    <Stack gap="lg">
      <CrawlPanel />
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          {/* Как устроен список — в «i» у заголовка, а не абзацем над таблицей: его
              читают один раз, а таблицу — каждый день (аудит экранов 09.10.2026). */}
          <PageHead title={LISTS[verdict].title} hint={LISTS[verdict].lead} />
          <SegmentedControl
            aria-label="Какой список смотреть"
            value={verdict}
            onChange={(picked) => setVerdict(picked as ReviewedVerdict)}
            data={[
              { value: 'pending', label: `Спорные · ${formatNumber(counts['pending'] ?? 0)}` },
              { value: 'bought', label: `Куплено · ${formatNumber(counts['bought'] ?? 0)}` },
            ]}
            style={{ alignSelf: 'flex-start' }}
          />
          <Group gap="xs">
            {(['bought', 'pending', 'skipped', 'blocked'] as const).map((verdict) => (
              <Badge key={verdict} variant="light" color={VERDICTS[verdict].color}>
                {VERDICTS[verdict].label}: {formatNumber(counts[verdict] ?? 0)}
              </Badge>
            ))}
          </Group>
          {data?.scoring !== undefined ? <FalseShare check={data.scoring} /> : null}
          <Group justify="space-between" align="center">
            <Text size="sm">
              Ждут человека: <b>{formatNumber(waiting)}</b>
              {decided > 0 ? (
                <>
                  , решено: <b>{formatNumber(decided)}</b>
                </>
              ) : null}
            </Text>
            <Switch
              size="sm"
              label="Показывать решённые"
              checked={includeDecided}
              onChange={(event) => setIncludeDecided(event.currentTarget.checked)}
            />
          </Group>
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
      <Card
        className="glassPanel staleRows"
        p={rows.length === 0 ? 'xl' : 'md'}
        data-stale={stale || undefined}
        aria-busy={stale || undefined}
        inert={stale}
      >
        {error ? (
          <Alert color="red" title="Очередь не загрузилась">
            {refusalOf(error)}
          </Alert>
        ) : rows.length === 0 ? (
          <EmptyQueue
            verdict={verdict}
            decided={decided}
            onShowDecided={includeDecided ? null : () => setIncludeDecided(true)}
          />
        ) : (
          <CandidatesTable
            rows={rows}
            mayDecide={mayDecide}
            deciding={decide.isPending ? (decide.variables?.row.id ?? null) : null}
            onDecide={(row, confirmed) => decide.mutate({ row, confirmed })}
          />
        )}
      </Card>
      <NicheQueue />
      <PromotePanel />
    </Stack>
  );
}

export function AdvertisersPage() {
  const [view, setView] = useAdvertiserView();
  return (
    <Stack gap="lg">
      <ViewSwitch view={view} onView={setView} />
      {view === 'review' ? <ReviewView /> : null}
      {view === 'list' ? <AdvertiserList /> : null}
      {view === 'acceptance' ? <AcceptanceReport /> : null}
    </Stack>
  );
}
