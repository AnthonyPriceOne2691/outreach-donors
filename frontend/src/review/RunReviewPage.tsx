/**
 * Рассмотрение прогона: принять или отклонить предложенных доноров.
 *
 * **Прогон кончается очередью, а не базой.** Пороги отвечают «годен ли
 * по цифрам», человек — «берём ли»: 23.09 пороги признали годными
 * microsoft.com и nih.gov. Контакты и письма получают только принятые.
 *
 * **Судья сортирует, а не решает.** Сверху — кому он советует принять,
 * сомнительные скрыты под переключателем со счётчиком: судья ошибается,
 * и отказ модели без глаз человека стоил бы донора.
 *
 * **Счёт «судья против человека» — наверху экрана**, по этому прогону; по
 * всем прогонам — готовность к автоприёму: совет «принять» верен в 95% на 200
 * решениях. До этого приём — только руками.
 *
 * **Смена вкладки — не перезагрузка** (замечание 25.09.2026). Раньше новая
 * вкладка меняла ключ запроса, и на время ответа весь экран заменялся
 * крутилкой и рисовался заново. Теперь верх экрана — заголовок, плитки
 * судьи, вкладки со счётчиками — стоит на месте, прежние строки видны
 * приглушёнными до прихода новых, а решать по ним нельзя: они не той
 * вкладки.
 *
 * **Над заголовком — «К прогонам»**, на ту страницу истории, с которой
 * пришли. Номер прогона из адреса проверяется до запроса: «abc» в адресе —
 * пустой экран со словами и ссылкой назад, а не английский отказ разбора.
 */

import {
  Alert,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';

import { refusalOf } from '../api/client';
import { countryTitle, REVIEW_DECISIONS } from '../api/labels';
import { decideCandidates, loadAccuracy, loadReview } from '../api/review';
import type { AccuracyView, AgreementView } from '../api/review';
import { KeywordYieldCard } from './KeywordYieldCard';
import type { ReviewDecision, ReviewView } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { ColumnsHead } from '../components/ColumnsHead';
import type { Column } from '../components/ColumnsHead';
import { Metric } from '../components/Metric';
import { ReserveLink } from '../reserve/ReserveLink';
import { missingRun, NoSuchRun, RunHead, useRunAddress } from '../runs/runAddress';
import { CandidateRow } from './CandidateRow';
import { DoubtfulNote, ShowDoubtful } from './DoubtfulNote';
import { BulkBar, TierRow } from './QueueParts';
import { TopicFilter } from '../topics/TopicFilter';
import { anyTopic, topicWords, useTopicFilter } from '../topics/topicRows';
import { notify } from '../notices';

const STATUSES = Object.keys(REVIEW_DECISIONS) as ReviewDecision[];

/** Сколько строк показывать за раз. Прогон даёт сотни кандидатов, и
 *  таблица в четыреста строк на одном экране — это прокрутка без конца
 *  и заметная пауза при каждом решении. */
const PAGE = 50;

const EMPTY: Record<ReviewDecision, string> = {
  pending: 'Предложенных нет: всё рассмотрено или скрыто как сомнительное.',
  accepted: 'Принятых пока нет.',
  rejected: 'Отклонённых нет.',
};

/** Колонки и их ширины — по самому длинному, что в них бывает, а не по
 *  сегодняшней странице (аудит 25.09.2026: значки судьи ужимались до
 *  «площа…» и «арб…»). Домену — остаток. Судья: самый длинный ряд значков
 *  «не площадка · продаёт размещение · арбитр» (~310 px) — в одну строку;
 *  «Донор ответил» — по значку «берёт бесплатно» (123 px шрифтом экрана,
 *  замер 26.09.2026: в прежних 9,5rem он резался до «берёт бесплат…»);
 *  решение — «Принять» и «Отклонить» рядом, с полями ячейки. */
const WIDTH = {
  pick: '3rem',
  keywords: '12rem',
  judge: '22rem',
  seller: '10rem',
  decision: '13.5rem',
} as const;

/** Уже этого таблица не сжимается и уезжает в прокрутку: колонкам — их
 *  ширины, домену — не меньше двухсот тридцати (строка метрик под ним). */
const MIN_WIDTH = { withKeywords: 1188, withoutKeywords: 998 } as const;

/** «Донор ответил» в пикселях — на столько уже таблица без этой колонки. */
const SELLER_PX = 160;

/** Колонки очереди слева направо. Отметка — у того, кто решает, и заголовок у неё —
 *  флажок «все на экране» (`heads`), а не слово. Число колонок для строки яруса —
 *  отсюда же: отдельный счёт рядом со списком разошёлся бы с шапкой на первой правке. */
function columnsOf({
  mayDecide,
  withKeywords,
  withAnswers,
}: {
  mayDecide: boolean;
  withKeywords: boolean;
  withAnswers: boolean;
}): Column[] {
  return [
    ...(mayDecide ? [{ title: 'Отметка', width: WIDTH.pick }] : []),
    { title: 'Домен' },
    ...(withKeywords ? [{ title: 'Нашёлся по ключам', width: WIDTH.keywords }] : []),
    { title: 'Судья', width: WIDTH.judge },
    ...(withAnswers ? [{ title: 'Донор ответил', width: WIDTH.seller }] : []),
    { title: 'Решение', width: WIDTH.decision },
  ];
}

/** Ответ вместе с тем, о чём спрашивали: пока идёт новая вкладка, видна
 *  прежняя, и пустой экран должен говорить про ту вкладку, строки которой
 *  на нём, а не про ту, что ещё едет. */
interface Shown {
  view: ReviewView;
  status: ReviewDecision;
}

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

/** «7 из 7» — сколько раз человек согласился с советом; советов нет — словами. */
function agreedOf(agreement: AgreementView | undefined): string {
  return agreement ? `${agreement.agreed} из ${agreement.advised}` : 'решений нет';
}

/** Точность советов судьи — по этому прогону; готовность к автоприёму — по всем
 *  прогонам: её решают на всей выборке, а не на одном прогоне. До 10.10.2026 все
 *  плитки шли по всем прогонам — «Решено человеком 33» на прогоне, где решено 7
 *  (проверка прода). По прогону счёт тот же, что у колонки истории. */
function JudgeScore({ run, overall }: { run: AccuracyView; overall: AccuracyView }) {
  const accept = run.by_advice.accept;
  const reject = run.by_advice.reject;
  const need = `≥ ${Math.round(overall.auto_accept_precision * 100)}% на ≥ ${overall.auto_accept_min_decisions}`;
  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
      <Metric
        title="Решено человеком"
        value={run.decided}
        hint={`судья просил посмотреть: ${run.asked_to_review}`}
      />
      <Metric
        title="Совет «площадка» верен"
        value={percent(accept?.precision ?? null)}
        hint={agreedOf(accept)}
      />
      <Metric
        title="Совет «не площадка» верен"
        value={percent(reject?.precision ?? null)}
        hint={agreedOf(reject)}
      />
      <Metric
        title="Автоприём по судье"
        value={overall.auto_accept_ready ? 'можно' : 'рано'}
        hint={`по всем прогонам ${agreedOf(overall.by_advice.accept)}; нужно ${need}`}
        color={overall.auto_accept_ready ? 'green' : undefined}
      />
    </SimpleGrid>
  );
}

export function RunReviewPage() {
  const { can } = useSession();
  const mayDecide = can('prices');
  const address = useRunAddress();
  const { runId, back } = address;
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ReviewDecision>('pending');
  const [showDoubtful, setShowDoubtful] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [shownCount, setShownCount] = useState(PAGE);
  // На узком окне три вкладки в ряд ужимали подписи до «Отклон…».
  const narrow = useMediaQuery('(max-width: 36em)');

  const review = useQuery({
    queryKey: ['review', runId, status, showDoubtful],
    queryFn: async (): Promise<Shown> => ({
      view: await loadReview(runId ?? 0, status, showDoubtful),
      status,
    }),
    enabled: runId !== null,
    // Смена вкладки не убирает экран: прежние строки стоят, пока едут новые.
    placeholderData: keepPreviousData,
  });
  // Плитки — этого прогона, автоприём — по всем: два запроса, один ключ сброса.
  const accuracy = useQuery({
    queryKey: ['review-accuracy', runId],
    queryFn: () => loadAccuracy(runId ?? 0),
    enabled: runId !== null,
  });
  const overall = useQuery({
    queryKey: ['review-accuracy', 'all'],
    queryFn: () => loadAccuracy(),
    enabled: runId !== null,
  });
  // Тематика — по строкам вкладки: очередь приходит целиком (`topics/topicRows.ts`).
  const topics = useTopicFilter(review.data?.view.rows);

  const decide = useMutation({
    mutationFn: ({ ids, decision }: { ids: number[]; decision: ReviewDecision }) =>
      decideCandidates(runId ?? 0, ids, decision),
    onSuccess: async (result, { decision }) => {
      setPicked(new Set());
      await queryClient.invalidateQueries({ queryKey: ['review', runId] });
      await queryClient.invalidateQueries({ queryKey: ['review-accuracy'] });
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
      notify({
        color: 'green',
        message:
          decision === 'accepted'
            ? `Принято: ${result.accepted}. Поиск контактов поставлен в очередь`
            : `${REVIEW_DECISIONS[decision].title}: ${result.changed}`,
      });
    },
    onError: (failure) =>
      notify({
        color: 'red',
        title: 'Решение не сохранено',
        message: refusalOf(failure),
      }),
  });

  const switchTo = (next: ReviewDecision) => {
    setPicked(new Set());
    setShownCount(PAGE);
    // Тематика — по строкам вкладки: на другой вкладке выбранной темы может не быть вовсе.
    topics.setTopic(null);
    setStatus(next);
  };

  // Сомнительные — переключателем над списком и кнопкой в его конце: отметки и страница —
  // заново, как при смене вкладки, — строки уже другие.
  const showDoubtfulRows = (on: boolean) => {
    setPicked(new Set());
    setShownCount(PAGE);
    setShowDoubtful(on);
  };
  const showTopic = (code: string | null) => {
    setPicked(new Set());
    setShownCount(PAGE);
    topics.setTopic(code);
  };

  const missing = missingRun(address, review.error);
  if (missing !== null) {
    return (
      <NoSuchRun back={back} said={missing} where="к рассмотрению ведёт кнопка в строке прогона." />
    );
  }

  // Прежний ответ годится, пока он про этот же прогон: переход из карточки
  // одного прогона в другой не должен показывать чужие строки под новым
  // заголовком.
  const shown = review.data?.view.run.id === runId ? review.data : null;
  const title = `Прогон №${runId}: рассмотрение`;

  if (shown === null) {
    // Первый ответ ещё не пришёл (или не пришёл вовсе): заголовок уже
    // стоит на своём месте, под ним — крутилка или отказ.
    return (
      <Stack gap="lg">
        <Card className="glassPanel" p="xl">
          <RunHead back={back} title={title} />
        </Card>
        {review.error ? (
          <Alert color="red" title="Очередь не загрузилась">
            {refusalOf(review.error)}
          </Alert>
        ) : (
          <Loader aria-label="Загружаем очередь" />
        )}
      </Stack>
    );
  }

  const { view } = shown;
  // Строки не той вкладки, что выбрана, — ждут замены: решать по ним нельзя.
  const stale = review.isPlaceholderData;
  const withKeywords = view.keywords !== null;
  // Колонки «Донор ответил» нет, пока никто из очереди не ответил: столбец «не отвечал»
  // в каждой строке держал 160 px (правило: колонка, которой нечего показать, не рисуется).
  const withAnswers = view.rows.some((row) => row.seller.answer !== null);
  const columns = columnsOf({ mayDecide, withKeywords, withAnswers });
  // Сколько строк в каждом ярусе — для разделителей групп: ярус стоит над группой
  // один раз, а не значком в каждой строке.
  const listed = topics.shown;
  const perTier = new Map<string, number>();
  for (const row of listed) perTier.set(row.tier, (perTier.get(row.tier) ?? 0) + 1);
  const rows = listed.slice(0, shownCount);
  const rest = listed.length - rows.length;
  const allPicked = rows.length > 0 && rows.every((row) => picked.has(row.candidate_id));
  const toggle = (id: number, on: boolean) =>
    setPicked((was) => {
      const next = new Set(was);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const bulk = (decision: ReviewDecision) => decide.mutate({ ids: [...picked], decision });
  const busy = decide.isPending || stale;
  const bulkShown = mayDecide && picked.size > 0 && !stale;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* Под заголовком — факты прогона; как устроен экран — в «i» (аудит 09.10.2026). */}
          <RunHead
            back={back}
            title={title}
            hint="Годные по порогам ждут решения: контакты ищутся и письма собираются только принятым. Судья сортирует очередь и подсказывает, но не решает; сомнительные скрыты, а не выброшены."
          >
            <Group gap="sm">
              <Text size="sm">
                {countryTitle(view.run.country)}, ключей {view.run.keywords}
              </Text>
              <ReserveLink runId={view.run.id}>Резерв прогона — не прошедшие</ReserveLink>
            </Group>
            {/* Пояснение там, где его видно, а не под полусотней строк: без
                него пропавшая колонка читалась бы как поломка. */}
            {!withKeywords && (
              <Text size="sm" c="dimmed" maw={720}>
                Какой ключ что нашёл, этот прогон не хранит — он запущен до того, как это стали
                записывать. Поэтому колонки «Нашёлся по ключам» здесь нет и отдачу его ключей не
                посчитать.
              </Text>
            )}
          </RunHead>
          {accuracy.data && overall.data && (
            <JudgeScore run={accuracy.data} overall={overall.data} />
          )}
        </Stack>
      </Card>

      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* По содержимому, а не во всю ширину: `Stack` растягивает и `inline-flex`
              (аудит экранов 09.10.2026). */}
          <SegmentedControl
            orientation={narrow ? 'vertical' : 'horizontal'}
            fullWidth={narrow}
            style={narrow ? undefined : { alignSelf: 'flex-start' }}
            value={status}
            onChange={(value) => switchTo(value as ReviewDecision)}
            data={STATUSES.map((value) => ({
              value,
              label: `${REVIEW_DECISIONS[value].title} — ${view.counts[value]}`,
            }))}
          />
          {/* Ряд под вкладками — только на «Предложенных»: на «Приняты» и
              «Отклонены» пустой ряд добавлял лишний отступ. */}
          {status === 'pending' && (
            <DoubtfulNote
              total={view.counts.pending}
              hidden={view.hidden}
              shown={showDoubtful}
              onShow={showDoubtfulRows}
            />
          )}
          {(anyTopic(topics.items) || topics.topic !== null) && (
            <TopicFilter value={topics.topic} items={topics.items} onChange={showTopic} />
          )}
        </Stack>
      </Card>

      <Card
        className="glass staleRows"
        p="xs"
        data-stale={stale || undefined}
        aria-busy={stale || undefined}
      >
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            {topics.topic === null
              ? EMPTY[shown.status]
              : `Под фильтр никто не попал: ${topicWords(topics.topic, topics.items)}. В списке — ${view.rows.length}.`}
          </Text>
        ) : (
          <Table.ScrollContainer
            minWidth={
              (withKeywords ? MIN_WIDTH.withKeywords : MIN_WIDTH.withoutKeywords) -
              (withAnswers ? 0 : SELLER_PX)
            }
            type="native"
            className="scrollSlim phoneCards"
          >
            <Table
              className={mayDecide ? 'dataTable pickFirst reviewTable' : 'dataTable reviewTable'}
              layout="fixed"
              verticalSpacing="sm"
              horizontalSpacing="md"
            >
              <ColumnsHead
                columns={columns}
                heads={{
                  Отметка: (
                    <Checkbox
                      aria-label="Выбрать все на экране"
                      checked={allPicked}
                      disabled={busy}
                      onChange={(event) =>
                        setPicked(
                          event.currentTarget.checked
                            ? new Set(rows.map((row) => row.candidate_id))
                            : new Set(),
                        )
                      }
                    />
                  ),
                }}
              />
              <Table.Tbody>
                {rows.map((row, at) => (
                  <Fragment key={row.candidate_id}>
                    {/* Ярус — разделителем над своей группой: очередь сервер отдаёт
                        по ярусам, и значок «посмотреть» в каждой строке повторял
                        совет судьи рядом (аудит экранов 09.10.2026). */}
                    {rows[at - 1]?.tier !== row.tier && (
                      <TierRow
                        tier={row.tier}
                        count={perTier.get(row.tier) ?? 0}
                        span={columns.length}
                      />
                    )}
                    <CandidateRow
                      row={row}
                      mayDecide={mayDecide}
                      withKeywords={withKeywords}
                      withAnswers={withAnswers}
                      busy={busy}
                      checked={picked.has(row.candidate_id)}
                      onCheck={(on) => toggle(row.candidate_id, on)}
                      onDecide={(decision) => decide.mutate({ ids: [row.candidate_id], decision })}
                    />
                  </Fragment>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
        {rest > 0 && (
          <Group justify="center" gap="sm" p="sm">
            <Text size="sm" c="dimmed">
              Показано {rows.length} из {listed.length}
            </Text>
            <Button variant="light" onClick={() => setShownCount((was) => was + PAGE)}>
              Показать ещё {Math.min(PAGE, rest)}
            </Button>
          </Group>
        )}
        <ShowDoubtful
          pending={status === 'pending'}
          shown={showDoubtful}
          hidden={view.hidden}
          rest={rest}
          onShow={() => showDoubtfulRows(true)}
        />
      </Card>

      {/* Решение пачкой — панелью, закреплённой внизу окна, пока есть отметки:
          над таблицей кнопки стояли в трёх тысячах пикселей от строк, отмеченных
          внизу очереди (аудит экранов 09.10.2026). */}
      {bulkShown && (
        <BulkBar
          count={picked.size}
          status={status}
          busy={decide.isPending}
          onDecide={bulk}
          onClear={() => setPicked(new Set())}
        />
      )}

      {/* Отдача ключей — для следующего прогона, поэтому под очередью.
          Прогон, который её не хранит, сказал об этом наверху. */}
      {withKeywords && (
        <Card className="glass" p="md">
          <KeywordYieldCard keywords={view.keywords ?? []} />
        </Card>
      )}
    </Stack>
  );
}
