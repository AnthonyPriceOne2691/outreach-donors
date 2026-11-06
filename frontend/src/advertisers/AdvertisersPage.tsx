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
 * именно им. Сверху — сводка обхода доноров (`CrawlSummary`): откуда кандидаты берутся.
 * Под очередью — бизнесы ниши из выдачи (`NicheQueue`), последним — «К письму»
 * (`PromotePanel`): перевод в рекламодатели и их адреса. Порядок блоков — порядок
 * работы (слово владельца 13.10.2026): перевод и поиск адресов — после всех решений выше.
 *
 * **Доля ложных — в шапке очереди, с допуском ТЗ** (`FalseShare`, сверка ТЗ
 * 11.10.2026): ради неё экран и сделан, а до этого её не было нигде. Рядом — доля «не пишем»
 * среди спорных выборки, ошибка базы «писать всем спорным», и под спорными — её разбивка по
 * баллу, признаку и нише (`DisputedStrata`, замер спорных 02.11.2026).
 *
 * **Виды — переключателем вверху** (`AdvertiserViews`): панель (`AdvertiserPanel`, вид по
 * умолчанию с 02.11.2026), проверка (всё выше, таблица очереди — `CandidatesTable`), все
 * рекламодатели списком с выгрузкой (`AdvertiserList`) и приёмка по донорам (`AcceptanceReport`).
 * С 29.10.2026 — ещё обход доноров своей вкладкой (`CrawlTab`): доноров тысячи, и в «Проверке»
 * осталась сводка (`CrawlSummary`).
 *
 * **Спорные — файлом** (`PendingExport`, тест-группа 31.10.2026): специалист проходит их таблицей.
 * Рядом — «Переобойти спорные» (`JudgePass`, 06.11.2026): совет судьи новым спорным кнопкой.
 *
 * **Очередь — страницами по десять, с фильтрами «Балл» и «За что» под колонками** (слово Anthony
 * 05.11.2026): сервер отдавал первые полсотни, и остальных не было видно вовсе. Список, фильтры и
 * страница — в адресе (`reviewFilters.ts`), смена любого из них — первая страница; решена последняя
 * строка последней страницы — экран уходит на новую последнюю (`usePagedQuery`). Числа в шапке — по
 * всем, от страницы и фильтра не меняются.
 *
 * **Обходы стоят без юнитов Ahrefs — плашка над всеми видами** (`UnitsStopAlert`, слово Anthony
 * 06.11.2026).
 */

import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  SegmentedControl,
  Stack,
  Switch,
  Text,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import type { ReviewedVerdict } from '../api/advertisers';
import { decideCandidate, fetchCandidates } from '../api/advertisers';
import type { CandidateCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { PageHead } from '../components/PageHead';
import { PageSwitch, useTurnToTop } from '../components/PageSwitch';
import { usePagedQuery, useUrlFilters } from '../components/useUrlFilters';
import { AcceptanceReport } from './AcceptanceReport';
import { AdvertiserList } from './AdvertiserList';
import { AdvertiserPanel } from './AdvertiserPanel';
import { useAdvertiserView, ViewSwitch } from './AdvertiserViews';
import { CandidatesTable, VERDICTS } from './CandidatesTable';
import { CrawlSummary } from './CrawlSummary';
import { UnitsStopAlert } from './UnitsStopAlert';
import { CrawlTab } from './CrawlTab';
import { DisputedStrata } from './DisputedStrata';
import { FalseShare } from './FalseShare';
import { JudgePassButton, JudgePassStatus } from './JudgePass';
import { NicheQueue } from './NicheQueue';
import { PendingExport } from './PendingExport';
import { PromotePanel } from './PromotePanel';
import {
  isReviewFiltered,
  readReviewFilters,
  reviewEmptiness,
  reviewQueryOf,
  writeReviewFilters,
} from './reviewFilters';
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
function ReviewView({ onOpenCrawl }: { onOpenCrawl: () => void }) {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const { filters, apply, turn } = useUrlFilters(readReviewFilters, writeReviewFilters);
  const { verdict, includeDecided } = filters;
  const asked = reviewQueryOf(filters);

  // Переключатель, фильтр и страница не перерисовывают экран: прежняя таблица стоит
  // приглушённой, пока не придёт новая. Раньше экран целиком сменялся значком
  // загрузки — «как будто страница загружается заново».
  const { query, data, settled, pages } = usePagedQuery(
    [...QUERY_KEY, 'queue', asked, filters.page],
    () => fetchCandidates(asked, filters.page),
    filters.page,
    turn,
  );
  const { isLoading, error } = query;
  const stale = query.isPlaceholderData;
  const { top, turnToTop } = useTurnToTop<HTMLDivElement>(turn);

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
  const filtered = isReviewFiltered(filters);
  // В списке никого — объяснение вместо таблицы; по числу всего списка, а не строк: пустая страница
  // за концом и прежний пустой ответ, пока едет сброс фильтра, — ещё не «никого». Под фильтром шапка
  // с полями остаётся: поправить условие можно тут же.
  const nothing = !filtered && data?.facets.listed === 0;

  return (
    <Stack gap="lg">
      <CrawlSummary onOpen={onOpenCrawl} />
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          {/* Как устроен список — в «i» у заголовка, а не абзацем над таблицей: его
              читают один раз, а таблицу — каждый день (аудит экранов 09.10.2026). */}
          <PageHead title={LISTS[verdict].title} hint={LISTS[verdict].lead} />
          <SegmentedControl
            aria-label="Какой список смотреть"
            value={verdict}
            onChange={(picked) =>
              apply({ verdict: picked as ReviewedVerdict, points: null, feature: null })
            }
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
          {verdict === 'pending' ? (
            <>
              <Group>
                <PendingExport total={counts['pending'] ?? 0} />
                <JudgePassButton />
              </Group>
              <JudgePassStatus />
              <DisputedStrata />
            </>
          ) : null}
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
              onChange={(event) => apply({ includeDecided: event.currentTarget.checked })}
            />
          </Group>
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
      <Card className="glassPanel" p={nothing ? 'xl' : 'md'} ref={top}>
        {error ? (
          <Alert color="red" title="Очередь не загрузилась">
            {refusalOf(error)}
          </Alert>
        ) : nothing || data === undefined ? (
          <Box className="staleRows" data-stale={stale || undefined} aria-busy={stale || undefined}>
            <EmptyQueue
              verdict={verdict}
              decided={decided}
              onShowDecided={includeDecided ? null : () => apply({ includeDecided: true })}
            />
          </Box>
        ) : (
          <Stack gap="sm">
            {/* Свернуть — там же, где развернули (замечание Anthony 27.10.2026): «Показать
                решённые» стоит в этой карточке, а переключатель — в карточке выше, у «Ждут
                человека», и после развёртывания свернуть было будто бы нечем. */}
            {includeDecided || filtered ? (
              <Group gap="sm">
                {includeDecided ? (
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    className="press"
                    onClick={() => apply({ includeDecided: false })}
                  >
                    Скрыть решённые
                  </Button>
                ) : null}
                {filtered ? (
                  // Одно на строке — с края текста колонок: поле карточки и поле ячейки.
                  <Text size="sm" pl={includeDecided ? 0 : 'md'}>
                    найдено {formatNumber(data.total)} из {formatNumber(data.facets.listed)}
                  </Text>
                ) : null}
              </Group>
            ) : null}
            <CandidatesTable
              rows={rows}
              stale={stale}
              empty={
                settled && filtered && data.total === 0
                  ? reviewEmptiness(filters, data.facets)
                  : null
              }
              onReset={() => apply({ points: null, feature: null })}
              filters={filters}
              facets={data.facets}
              onFilter={apply}
              mayDecide={mayDecide}
              deciding={decide.isPending ? (decide.variables?.row.id ?? null) : null}
              onDecide={(row, confirmed) => decide.mutate({ row, confirmed })}
            />
            <PageSwitch
              label="Страницы очереди проверки"
              page={filters.page}
              pages={pages}
              onChange={turnToTop}
            />
          </Stack>
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
      <UnitsStopAlert />
      {view === 'panel' ? <AdvertiserPanel /> : null}
      {view === 'review' ? <ReviewView onOpenCrawl={() => setView('crawl')} /> : null}
      {view === 'crawl' ? <CrawlTab /> : null}
      {view === 'list' ? <AdvertiserList /> : null}
      {view === 'acceptance' ? <AcceptanceReport /> : null}
    </Stack>
  );
}
