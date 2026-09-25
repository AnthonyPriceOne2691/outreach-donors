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
 * **Счёт «судья против человека» — наверху экрана.** По нему решают,
 * когда судье можно доверить приём: совет «принять» верен в 95% на 200
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
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { countryTitle, REVIEW_DECISIONS } from '../api/labels';
import { decideCandidates, loadAccuracy, loadReview } from '../api/review';
import { KeywordYieldCard } from './KeywordYieldCard';
import type { AccuracyView, ReviewDecision, ReviewView } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { BackLink, backTo } from '../components/BackLink';
import { Metric } from '../components/Metric';
import { CandidateRow } from './CandidateRow';

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
 *  «Донор ответил» — заголовок в одну строку; решение — «Принять»
 *  и «Отклонить» рядом, с полями ячейки. */
const WIDTH = {
  pick: '3rem',
  keywords: '12rem',
  judge: '22rem',
  seller: '9.5rem',
  decision: '13.5rem',
} as const;

/** Уже этого таблица не сжимается и уезжает в прокрутку: колонкам — их
 *  ширины, домену — не меньше двухсот тридцати (строка метрик под ним). */
const MIN_WIDTH = { withKeywords: 1180, withoutKeywords: 990 } as const;

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

/** Точность советов судьи и готовность к автоприёму — словами и числами. */
function JudgeScore({ accuracy }: { accuracy: AccuracyView }) {
  const accept = accuracy.by_advice.accept;
  const reject = accuracy.by_advice.reject;
  const need = `≥ ${Math.round(accuracy.auto_accept_precision * 100)}% на ≥ ${accuracy.auto_accept_min_decisions}`;
  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
      <Metric
        title="Решено человеком"
        value={accuracy.decided}
        hint={`судья просил посмотреть: ${accuracy.asked_to_review}`}
      />
      <Metric
        title="Совет «площадка» верен"
        value={percent(accept?.precision ?? null)}
        hint={accept ? `${accept.agreed} из ${accept.advised}` : 'решений нет'}
      />
      <Metric
        title="Совет «не площадка» верен"
        value={percent(reject?.precision ?? null)}
        hint={reject ? `${reject.agreed} из ${reject.advised}` : 'решений нет'}
      />
      <Metric
        title="Автоприём по судье"
        value={accuracy.auto_accept_ready ? 'можно' : 'рано'}
        hint={`нужно ${need}`}
        color={accuracy.auto_accept_ready ? 'green' : undefined}
      />
    </SimpleGrid>
  );
}

/** Верх экрана: возврат, заголовок, пояснение. Стоит и пока очередь едет —
 *  номер прогона известен из адреса. */
function Head({ back, title, children }: { back: string; title: string; children?: ReactNode }) {
  return (
    <Stack gap={6}>
      <BackLink to={back}>К прогонам</BackLink>
      <Title order={3}>{title}</Title>
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
  const accuracy = useQuery({
    queryKey: ['review-accuracy'],
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
      notifications.show({
        color: 'green',
        message:
          decision === 'accepted'
            ? `Принято: ${result.accepted}. Поиск контактов поставлен в очередь`
            : `${REVIEW_DECISIONS[decision].title}: ${result.changed}`,
      });
    },
    onError: (failure) =>
      notifications.show({
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
          <Head back={back} title={title}>
            <Text size="sm" c="dimmed" maw={720}>
              {countryTitle(view.run.country)}, ключей {view.run.keywords}. Годные по порогам ждут
              решения: контакты ищутся и письма собираются только принятым. Судья сортирует очередь
              и подсказывает, но не решает; сомнительные скрыты, а не выброшены.
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
          {accuracy.data && <JudgeScore accuracy={accuracy.data} />}
        </Stack>
      </Card>

      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <SegmentedControl
            orientation={narrow ? 'vertical' : 'horizontal'}
            fullWidth={narrow}
            value={status}
            onChange={(value) => switchTo(value as ReviewDecision)}
            data={STATUSES.map((value) => ({
              value,
              label: `${REVIEW_DECISIONS[value].title} — ${view.counts[value]}`,
            }))}
          />
          {/* Ряд под вкладками — только когда в нём что-то есть: пустой ряд
              добавлял под вкладками «Приняты» и «Отклонены» лишний отступ. */}
          {(status === 'pending' || bulkShown) && (
            <Group justify="space-between" align="center">
              {status === 'pending' ? (
                <Switch
                  label={`Показать сомнительные (скрыто ${view.hidden})`}
                  checked={showDoubtful}
                  onChange={(event) => {
                    setPicked(new Set());
                    setShownCount(PAGE);
                    setShowDoubtful(event.currentTarget.checked);
                  }}
                />
              ) : (
                <span />
              )}
              {bulkShown && (
                <Group gap="sm">
                  <Text size="sm">Выбрано: {picked.size}</Text>
                  {status === 'pending' ? (
                    <>
                      <Button
                        color="green"
                        loading={decide.isPending}
                        onClick={() => bulk('accepted')}
                      >
                        Принять выбранные
                      </Button>
                      <Button
                        variant="default"
                        loading={decide.isPending}
                        onClick={() => bulk('rejected')}
                      >
                        Отклонить выбранные
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="default"
                      loading={decide.isPending}
                      onClick={() => bulk('pending')}
                    >
                      Вернуть выбранные
                    </Button>
                  )}
                </Group>
              )}
            </Group>
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
            minWidth={withKeywords ? MIN_WIDTH.withKeywords : MIN_WIDTH.withoutKeywords}
            type="native"
            className="scrollSlim"
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
                <col style={{ width: WIDTH.seller }} />
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
                  <Table.Th>Донор ответил</Table.Th>
                  <Table.Th>Решение</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <CandidateRow
                    key={row.candidate_id}
                    row={row}
                    mayDecide={mayDecide}
                    withKeywords={withKeywords}
                    busy={busy}
                    checked={picked.has(row.candidate_id)}
                    onCheck={(on) => toggle(row.candidate_id, on)}
                    onDecide={(decision) => decide.mutate({ ids: [row.candidate_id], decision })}
                  />
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
