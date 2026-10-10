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
  Switch,
  Table,
  Text,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { countryTitle, REVIEW_DECISIONS } from '../api/labels';
import { decideCandidates, loadAccuracy, loadReview } from '../api/review';
import { KeywordYieldCard } from './KeywordYieldCard';
import type { AccuracyView, AgreementView, ReviewDecision, ReviewView } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { BackLink, backTo } from '../components/BackLink';
import { Metric } from '../components/Metric';
import { PageHead } from '../components/PageHead';
import { CandidateRow } from './CandidateRow';
import { BulkBar, TierRow } from './QueueParts';
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

/** Верх экрана: возврат, заголовок, пояснение. Стоит и пока очередь едет —
 *  номер прогона известен из адреса. */
function Head({
  back,
  title,
  hint,
  children,
}: {
  back: string;
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <Stack gap={6}>
      <BackLink to={back}>К прогонам</BackLink>
      <PageHead title={title} {...(hint === undefined ? {} : { hint })} />
      {children}
    </Stack>
  );
}

/** Прогона нет: номер негодный или такого нет в базе. Словами и со ссылкой
 *  назад — пустое полотно или отказ разбора ничего не объясняют. */
function NoSuchRun({ back, said }: { back: string; said: string }) {
  return (
    <Card className="glassPanel" p="xl">
      <Head back={back} title="Такого прогона нет">
        <Text size="sm" c="dimmed" maw={720}>
          {said} Все прогоны — в истории на экране «Прогон»: к рассмотрению ведёт кнопка в строке
          прогона.
        </Text>
      </Head>
    </Card>
  );
}

export function RunReviewPage() {
  const { can } = useSession();
  const mayDecide = can('prices');
  const raw = useParams().id;
  const runId = rowIdOf(raw);
  const back = `/run${backTo(useLocation().state)}`;
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
    setStatus(next);
  };

  if (runId === null) {
    return <NoSuchRun back={back} said={`«${raw ?? ''}» в адресе — не номер прогона.`} />;
  }
  if (review.error instanceof ApiError && review.error.status === 404) {
    return <NoSuchRun back={back} said={`${refusalOf(review.error)}.`} />;
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
          <Head back={back} title={title} />
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
  const columns = (mayDecide ? 1 : 0) + 2 + (withKeywords ? 1 : 0) + (withAnswers ? 1 : 0) + 1;
  // Сколько строк в каждом ярусе — для разделителей групп: ярус стоит над группой
  // один раз, а не значком в каждой строке.
  const perTier = new Map<string, number>();
  for (const row of view.rows) perTier.set(row.tier, (perTier.get(row.tier) ?? 0) + 1);
  const rows = view.rows.slice(0, shownCount);
  const rest = view.rows.length - rows.length;
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
          <Head
            back={back}
            title={title}
            hint="Годные по порогам ждут решения: контакты ищутся и письма собираются только принятым. Судья сортирует очередь и подсказывает, но не решает; сомнительные скрыты, а не выброшены."
          >
            <Text size="sm">
              {countryTitle(view.run.country)}, ключей {view.run.keywords}
            </Text>
            {/* Пояснение там, где его видно, а не под полусотней строк: без
                него пропавшая колонка читалась бы как поломка. */}
            {!withKeywords && (
              <Text size="sm" c="dimmed" maw={720}>
                Какой ключ что нашёл, этот прогон не хранит — он запущен до того, как это стали
                записывать. Поэтому колонки «Нашёлся по ключам» здесь нет и отдачу его ключей не
                посчитать.
              </Text>
            )}
          </Head>
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
            <Switch
              label={`Показать сомнительные (скрыто ${view.hidden})`}
              checked={showDoubtful}
              onChange={(event) => {
                setPicked(new Set());
                setShownCount(PAGE);
                setShowDoubtful(event.currentTarget.checked);
              }}
            />
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
            {EMPTY[shown.status]}
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
              <colgroup>
                {mayDecide && <col style={{ width: WIDTH.pick }} />}
                <col />
                {withKeywords && <col style={{ width: WIDTH.keywords }} />}
                <col style={{ width: WIDTH.judge }} />
                {withAnswers && <col style={{ width: WIDTH.seller }} />}
                <col style={{ width: WIDTH.decision }} />
              </colgroup>
              <Table.Thead>
                <Table.Tr>
                  {mayDecide && (
                    <Table.Th>
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
                    </Table.Th>
                  )}
                  <Table.Th>Домен</Table.Th>
                  {withKeywords && <Table.Th>Нашёлся по ключам</Table.Th>}
                  <Table.Th>Судья</Table.Th>
                  {withAnswers && <Table.Th>Донор ответил</Table.Th>}
                  <Table.Th>Решение</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row, at) => (
                  <Fragment key={row.candidate_id}>
                    {/* Ярус — разделителем над своей группой: очередь сервер отдаёт
                        по ярусам, и значок «посмотреть» в каждой строке повторял
                        совет судьи рядом (аудит экранов 09.10.2026). */}
                    {rows[at - 1]?.tier !== row.tier && (
                      <TierRow tier={row.tier} count={perTier.get(row.tier) ?? 0} span={columns} />
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
              Показано {rows.length} из {view.rows.length}
            </Text>
            <Button variant="light" onClick={() => setShownCount((was) => was + PAGE)}>
              Показать ещё {Math.min(PAGE, rest)}
            </Button>
          </Group>
        )}
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
