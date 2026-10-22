/**
 * Пересуд прогона после правки описания сегмента: смета и подтверждение, ход «пересуд: X из Y»,
 * итог «было → стало».
 *
 * Инструмент калибровки судьи: описание поправили — те же сайты и тот же текст спрашиваются
 * заново, без новой выдачи. **Сначала смета** — окно со словами сервера (сайтов, к модели,
 * токенов, кого пересуд не тронет), запуск — только по смете того описания, что видел человек;
 * отказ сервера — в окне, над кнопками. **Ход** опрашивается, пока задача идёт; задача пропала —
 * сказано словами, а не вечное «идёт». **Итог** — сколько сайтов сменили полосу и как, у сайтов с
 * лидом — «стало бы», сами сайты — вкладкой «Сменили при пересуде».
 *
 * Пересуд тратит модель — кнопка только с правом «запускать прогоны», без него — кто пересуживает.
 *
 * **Незаконченный пересуд того же описания продолжается** с места остановки (потолок модели,
 * отказ, умер воркер) — и экран так и говорит: «Продолжить пересуд», в окне — сколько осталось.
 *
 * **Где что** (отчёт прогона компактно, 15.10.2026): кнопка, ход и итог — в окне «Сайты прогона»;
 * в отчёте — ход и остановка одной строкой (без `onChanged`), а следит за пересудом отчёт
 * (`useRejudgeWatch`): окно могут закрыть посреди пересуда, а плитки обязаны обновиться. Окно
 * сметы встаёт поверх окна сайтов, и Esc закрывает только его (`onWindow`): у Mantine Esc слушает
 * каждое открытое окно.
 */

import { Alert, Button, Group, List, Loader, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
import { notify } from '../notices';
import { quoteRetries, quoteRetryWords, SEGMENT_QUERY_KEY } from './segmentData';
import { readRejudge, startRejudge } from './SegmentSitesApi';
import type { RejudgeLast, RejudgeView } from './SegmentSitesApi';
import { moveWords, personOf } from './SegmentSitesWords';

export const REJUDGE_QUERY_KEY = ['sales', 'segment-rejudge'] as const;
export const SITES_QUERY_KEY = ['sales', 'segment-sites'] as const;

/** Фокус — на кнопку, когда окно сметы ушло: та же пауза, что у окон Mantine. */
const RETURN_FOCUS_MS = 10;

/** Задача пересуда ещё идёт: отчёт говорит «идёт», и очередь не сказала, что задачи нет. */
function moving(view: RejudgeView | undefined): boolean {
  const state = view?.last?.state;
  return (state === 'queued' || state === 'going') && view?.going !== false;
}

/** Пересуд прогона: смета, ход и итог. Опрос — пока задача идёт. */
export function useRejudge(run: number, enabled: boolean) {
  return useQuery({
    queryKey: [...REJUDGE_QUERY_KEY, run],
    queryFn: () => readRejudge(run),
    enabled,
    refetchInterval: (query) => (moving(query.state.data) ? 3_000 : false),
  });
}

/** Кончился пересуд — отчёт прогона и список сайтов перечитываются: полосы уже другие. */
function useSettled(view: RejudgeView | undefined) {
  const client = useQueryClient();
  const was = useRef(false);
  const now = moving(view);
  useEffect(() => {
    if (was.current && !now) {
      void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
      void client.invalidateQueries({ queryKey: SITES_QUERY_KEY });
    }
    was.current = now;
  }, [client, now]);
}

/**
 * Пересуд прогона глазами отчёта: ход спрашивается, если пересуд был или его только что поставили,
 * и кончился — отчёт и список сайтов перечитаны. Живёт в отчёте, а не в окне сайтов: окно
 * закрыли посреди пересуда — плитки всё равно обновятся, когда он кончится.
 */
export function useRejudgeWatch(run: number, asked: boolean) {
  const [started, setStarted] = useState(false);
  const found = useRejudge(run, started || asked);
  useSettled(found.data);
  return { view: found.data, onStarted: () => setStarted(true) };
}

function Estimate({ view }: { view: RejudgeView }) {
  const { estimate } = view;
  return (
    <Stack gap="xs">
      <List size="sm" spacing={4} aria-label="Смета пересуда">
        {estimate.lines.map((line) => (
          <List.Item key={line}>{line}</List.Item>
        ))}
      </List>
      {estimate.refusal !== null && (
        <Alert color="red" title="Пересуд не запустить">
          {estimate.refusal}
        </Alert>
      )}
    </Stack>
  );
}

interface ModalProps {
  run: number;
  onClose: () => void;
  /** Пересуд поставлен: экран начинает спрашивать его ход. */
  onStarted: () => void;
}

/** Слова окна по смете сервера: новый пересуд — или продолжение незаконченного. */
function wordsOf(run: number, view: RejudgeView | undefined) {
  const count =
    view !== undefined && view.estimate.sites > 0 ? ` · ${formatNumber(view.estimate.sites)}` : '';
  if (view?.estimate.resumes === true) {
    return {
      title: `Продолжить пересуд прогона №${run}`,
      intro: 'Пересуд того же описания не закончен — продолжится с места остановки.',
      confirm: `Продолжить${count}`,
    };
  }
  return {
    title: `Пересудить прогон №${run}`,
    intro: 'Те же сайты и тот же текст — новый вопрос судье по нынешнему описанию сегмента.',
    confirm: `Пересудить${count}`,
  };
}

/** Смета в окне — или почему её нет, или что она считается. */
function EstimateOrWait({ view, error }: { view: RejudgeView | undefined; error: Error | null }) {
  if (view !== undefined) return <Estimate view={view} />;
  if (error !== null) {
    return (
      <Alert color="red" title="Смета не посчиталась">
        {refusalOf(error)}
      </Alert>
    );
  }
  return <Loader aria-label="Считаем смету пересуда" size="sm" />;
}

function RejudgeModal({ run, onClose, onStarted }: ModalProps) {
  const client = useQueryClient();
  // Смета — всегда свежая: описание могли поправить минуту назад.
  const found = useQuery({
    queryKey: [...REJUDGE_QUERY_KEY, run, 'estimate'],
    queryFn: () => readRejudge(run),
    gcTime: 0,
  });
  const start = useMutation({
    mutationFn: (digest: string) => startRejudge(run, digest),
    onSuccess: () => {
      // Ход — свежим запросом; смета окна (свой ключ) не перечитывается: окно закрывается.
      void client.invalidateQueries({ queryKey: [...REJUDGE_QUERY_KEY, run], exact: true });
      notify({ message: `Пересуд прогона №${run} поставлен`, color: 'green' });
      onStarted();
      onClose();
    },
  });
  const view = found.data;
  const ready = view !== undefined && view.estimate.refusal === null && view.estimate.sites > 0;
  const words = wordsOf(run, view);
  return (
    <Modal opened onClose={onClose} title={words.title} size="lg">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          {words.intro}
        </Text>
        {start.error !== null && (
          <Alert color="red" title="Пересуд не запущен">
            {refusalOf(start.error)}
          </Alert>
        )}
        <EstimateOrWait view={view} error={found.error} />
        <Group justify="flex-end" gap="sm">
          <Button variant="subtle" className="press" disabled={start.isPending} onClick={onClose}>
            Отмена
          </Button>
          <Button
            className="press"
            disabled={!ready}
            loading={start.isPending}
            onClick={() => view !== undefined && start.mutate(view.estimate.digest)}
          >
            {words.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** «Пересудить прогон» — с правом «запускать прогоны»; без него — кто пересуживает. Незаконченный
 *  пересуд того же описания — «Продолжить пересуд»: он пойдёт с места остановки. */
export function RejudgeButton({
  run,
  resumes,
  onStarted,
  onWindow,
}: {
  run: number;
  resumes: boolean;
  onStarted: () => void;
  /** Окно сметы открыто или закрыто: окно сайтов под ним не закрывается своим Esc. */
  onWindow?: (opened: boolean) => void;
}) {
  const { can } = useSession();
  const [opened, setOpened] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  // Ушла кнопка вместе с окном сайтов — окна сметы тоже нет.
  useEffect(() => {
    onWindow?.(opened);
    return () => onWindow?.(false);
  }, [onWindow, opened]);
  // Окно сметы снимается целиком (смета — свежая на каждое открытие), и само окно фокус не
  // вернёт: Mantine возвращает его, только когда окно закрывают, а не снимают. Возврат — на
  // кнопку, после того как окно ушло, как у Mantine.
  const close = () => {
    setOpened(false);
    window.setTimeout(() => button.current?.focus({ preventScroll: true }), RETURN_FOCUS_MS);
  };
  if (!can('run')) {
    return (
      <Text size="sm" c="dimmed">
        Пересуживает сотрудник с правом «{PERMISSION_TITLES.run}».
      </Text>
    );
  }
  return (
    <>
      <Button
        ref={button}
        variant="default"
        className="press"
        aria-haspopup="dialog"
        onClick={() => setOpened(true)}
      >
        {resumes ? 'Продолжить пересуд' : 'Пересудить прогон'}
      </Button>
      {opened ? <RejudgeModal run={run} onClose={close} onStarted={onStarted} /> : null}
    </>
  );
}

/** «Было → стало» строками: «принят → отсечён — 5»; у сайтов с лидом — «стало бы». */
function Moves({ last }: { last: RejudgeLast }) {
  if (last.moved.length === 0) return null;
  return (
    <List size="sm" spacing={2} aria-label="Было → стало">
      {last.moved.map((move) => (
        <List.Item key={`${move.was}-${move.now}-${String(move.would)}`}>
          {move.would ? 'стало бы у сайтов с лидом: ' : ''}
          {moveWords(move)} — {formatNumber(move.count)}
        </List.Item>
      ))}
    </List>
  );
}

/** Числа пересуда, о которых надо знать: кто остался за человеком, что не вышло и сколько стоило —
 *  второй вопрос модели о цитате (судья v5) идёт рядом с токенами: его токены — в них. */
function aside(last: RejudgeLast): string[] {
  const lines: string[] = [];
  if (last.human > 0) {
    lines.push(`решил человек посреди пересуда — ${formatNumber(last.human)}: решение его`);
  }
  if (last.unanswered > 0) {
    lines.push(`модель не ответила — ${formatNumber(last.unanswered)}: эти сайты на проверке`);
  }
  if (last.failed > 0)
    lines.push(`сбой на сайтах — ${formatNumber(last.failed)}: их полосы прежние`);
  const retries = quoteRetries(last);
  if (retries !== null) lines.push(quoteRetryWords(retries));
  if (last.tokens > 0) lines.push(`токенов модели — ${formatNumber(last.tokens)}`);
  return lines;
}

function Done({ last, onChanged }: { last: RejudgeLast; onChanged: () => void }) {
  const changed = last.moved.filter((move) => !move.would).reduce((sum, m) => sum + m.count, 0);
  const who = last.by !== null ? ` · ${personOf(last.by)}` : '';
  return (
    <Stack gap={4} role="status">
      <Text size="sm">
        Пересуд {formatDateTime(last.finished_at)}
        {who}: сменили полосу {formatNumber(changed)} из {formatNumber(last.total)}.
      </Text>
      <Moves last={last} />
      {aside(last).map((line) => (
        <Text key={line} size="sm" c="dimmed">
          {line}
        </Text>
      ))}
      {last.moved.length > 0 && (
        <Group>
          <Button variant="subtle" size="compact-sm" className="press" onClick={onChanged}>
            Показать сменившие
          </Button>
        </Group>
      )}
    </Stack>
  );
}

/** Что будет по «Пересудить»: незаконченный пересуд того же описания продолжится с места, иначе —
 *  новый со своим снимком (описание поправили). Слова — по смете сервера, а не догадкой экрана. */
function goOn(view: RejudgeView, where: 'обрыва' | 'остановки'): string {
  return view.estimate.resumes
    ? `«Продолжить пересуд» пойдёт с места ${where}: пересуженные сайты к модели второй раз не пойдут.`
    : 'Описание с тех пор поправили — пересуд начнётся заново, со своим снимком полос.';
}

/** «Сделано X из Y» — одним видом у хода, обрыва и остановки. */
function counted(last: RejudgeLast): string {
  return `${formatNumber(last.done)} из ${formatNumber(last.total)}`;
}

/** Задача ждёт повтора после временного сбоя: почему и когда — словами очереди. */
function retryWords(view: RejudgeView): string | null {
  const job = view.job;
  if (job?.state !== 'retry_wait') return null;
  const why = job.error !== null ? `: ${job.error}` : '';
  const when =
    job.next_try_at !== null ? ` — следующая попытка ${formatDateTime(job.next_try_at)}` : '';
  return `Пересуд ждёт повтора${why}${when}.`;
}

/** Пересуд поставлен или идёт: ход — или почему его не узнать, или что задача пропала. */
function Going({ view, last }: { view: RejudgeView; last: RejudgeLast }) {
  if (view.going === null) {
    return (
      <Text size="sm" c="dimmed" role="status">
        Очередь не отвечает — ход пересуда сейчас не узнать.
      </Text>
    );
  }
  if (!view.going) {
    const why = view.job?.error != null ? `упала: ${view.job.error}` : 'пропала';
    return (
      <Alert color="yellow" title="Пересуд оборвался">
        Задача пересуда {why} — сделано {counted(last)}. {goOn(view, 'обрыва')}
      </Alert>
    );
  }
  const retry = retryWords(view);
  if (retry !== null) {
    return (
      <Text size="sm" role="status">
        {retry}
      </Text>
    );
  }
  return (
    <Group gap="xs" role="status">
      <Loader size="xs" aria-hidden="true" />
      <Text size="sm">
        {last.state === 'queued'
          ? 'Пересуд поставлен — ждёт очереди задач'
          : `Пересуд: ${counted(last)}`}
      </Text>
    </Group>
  );
}

/** Пересуд поставлен, идёт или стоит — ход или что ждёт человека. */
function Unsettled({ view, last }: { view: RejudgeView; last: RejudgeLast }) {
  if (last.state !== 'stopped') return <Going view={view} last={last} />;
  return (
    <Alert color="yellow" title="Пересуд остановлен">
      {last.error ?? 'причина не записана'} — сделано {counted(last)}. {goOn(view, 'остановки')}
    </Alert>
  );
}

/** Ход и итог последнего пересуда — словами. Без `onChanged` (отчёт прогона) — только ход и
 *  остановка: итог «было → стало» — в окне сайтов, рядом с вкладкой сменивших. */
export function RejudgeStatus({
  view,
  onChanged,
}: {
  view: RejudgeView | undefined;
  /** «Показать сменившие» — вкладка сменивших в окне сайтов. */
  onChanged?: () => void;
}) {
  const last = view?.last ?? null;
  if (view === undefined || last === null) return null;
  if (last.state !== 'done') return <Unsettled view={view} last={last} />;
  return onChanged === undefined ? null : <Done last={last} onChanged={onChanged} />;
}
