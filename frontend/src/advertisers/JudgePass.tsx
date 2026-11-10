/**
 * «Переобойти спорные»: судья заново смотрит страницу со ссылкой и сайт рекламодателя и ставит совет.
 * Смета и подтверждение (`JudgePassModal`), ход «X из Y», итог словами (`api/advertisers/judge_pass.py`).
 *
 * **Кнопка и ход — порознь**: кнопка стоит рядом с выгрузкой спорных, ход и итог — строкой под ними.
 * Запрос один на оба (`JUDGE_PASS_KEY`) и опрашивается, только пока разбор идёт. Кончился — очередь
 * перечитывается: совет виден под решением человека.
 *
 * **Незаконченный разбор продолжается**: уже разобранных план не берёт, поэтому после остановки
 * кнопка говорит «Продолжить разбор», а смета — сколько осталось.
 *
 * Разбор тратит модель — кнопка только с правом «запускать прогоны», без него — кто запускает.
 *
 * **Итог — одной строкой, отчёт — в «i»** (разведка экранов 10.11.2026: две строки мелкого текста
 * с исходами, кто запускал и токенами): на виду — когда и что посоветовал судья, «Разбор
 * 03.11.2026, 12:00: советов 12 (пишем 7 · не пишем 3 · не понять 2)»; строки сервера и кто
 * запускал — по нажатию. Итог, записанный до чисел советов, — «Разбор окончен» и те же строки в «i».
 */

import { Alert, Button, Group, Loader, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { fetchJudgePass, JUDGE_PASS_KEY, passMoving, passResumes } from '../api/judgePass';
import type { JudgePassView } from '../api/judgePass';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { InfoHint } from '../components/InfoHint';
import { formatDateTime, formatNumber } from '../format';
import { ADVICE } from './CandidatesTable';
import { JudgePassModal } from './JudgePassModal';

/** Очередь проверки: совет судьи виден в ней под решением. */
const QUEUE_KEY = ['advertisers'] as const;
const POLL_MS = 5_000;

function useJudgePass() {
  return useQuery({
    queryKey: JUDGE_PASS_KEY,
    queryFn: fetchJudgePass,
    refetchInterval: (query) => (passMoving(query.state.data) ? POLL_MS : false),
  });
}

/** Кнопка с окном сметы — с правом «запускать прогоны»; без него — кто запускает. */
export function JudgePassButton() {
  const { can } = useSession();
  const [opened, setOpened] = useState(false);
  const { data: view } = useJudgePass();
  if (!can('run')) {
    return (
      <Text size="sm" c="dimmed">
        Переобходит спорных сотрудник с правом «{PERMISSION_TITLES.run}».
      </Text>
    );
  }
  return (
    <>
      <Button
        variant="default"
        size="xs"
        className="press"
        aria-haspopup="dialog"
        disabled={passMoving(view)}
        onClick={() => setOpened(true)}
      >
        {passResumes(view) ? 'Продолжить разбор' : 'Переобойти спорные'}
      </Button>
      {opened ? <JudgePassModal onClose={() => setOpened(false)} /> : null}
    </>
  );
}

/** Кончился разбор — очередь перечитывается: под решениями появились новые советы. */
function useSettled(view: JudgePassView | undefined) {
  const client = useQueryClient();
  const was = useRef(false);
  const now = passMoving(view);
  useEffect(() => {
    if (was.current && !now) void client.invalidateQueries({ queryKey: QUEUE_KEY });
    was.current = now;
  }, [client, now]);
}

function counted(view: JudgePassView): string {
  return `${formatNumber(view.done)} из ${formatNumber(view.total)}`;
}

function Going({ view }: { view: JudgePassView }) {
  return (
    <Group gap="xs" role="status">
      <Loader size="xs" aria-hidden="true" />
      <Text size="sm">
        {view.state === 'queued'
          ? 'Разбор поставлен: ждёт свободного обходчика'
          : `Судья разбирает спорные: ${counted(view)}`}
      </Text>
    </Group>
  );
}

/** Порядок советов в строке итога — от «пишем» к «не понять». */
const ADVICE_ORDER = ['write', 'skip', 'unclear'] as const;

/** «советов 23 (пишем 13 · не пишем 5 · не понять 5)»: нулевой совет не называется. */
function adviceWords(advice: NonNullable<JudgePassView['advice']>): string {
  const total = ADVICE_ORDER.reduce((sum, key) => sum + (advice[key] ?? 0), 0);
  if (total === 0) return 'советов нет';
  const parts = ADVICE_ORDER.filter((key) => (advice[key] ?? 0) > 0).map(
    (key) => `${ADVICE[key]}\u00a0${formatNumber(advice[key] ?? 0)}`,
  );
  return `советов ${formatNumber(total)} (${parts.join(' · ')})`;
}

function Done({ view }: { view: JudgePassView }) {
  const when = view.ended_at !== null ? ` ${formatDateTime(view.ended_at)}` : '';
  if (view.lines.length === 0) {
    return (
      <Text size="sm" c="dimmed" role="status">
        Разбор{when}: разбирать было некого
      </Text>
    );
  }
  const said =
    view.advice == null ? `Разбор окончен${when}` : `Разбор${when}: ${adviceWords(view.advice)}`;
  return (
    <Group gap={4} align="center" wrap="nowrap">
      <Text size="sm" c="dimmed" role="status">
        {said}
      </Text>
      <InfoHint name="Итог разбора целиком" width={340} interactive>
        <Stack gap={4}>
          {view.by !== null ? <Text size="sm">Запускал: {view.by}</Text> : null}
          {view.lines.map((line) => (
            <Text key={line} size="sm">
              {line}
            </Text>
          ))}
        </Stack>
      </InfoHint>
    </Group>
  );
}

function Broke({ view, title }: { view: JudgePassView; title: string }) {
  const done = view.total > 0 ? ` Сделано ${counted(view)}.` : '';
  return (
    <Alert color="yellow" title={title}>
      {view.why ?? 'причина не записана'}.{done} «Продолжить разбор» пойдёт с места: уже разобранных
      модель второй раз не спросит.
    </Alert>
  );
}

/** Ход и итог последнего разбора — строкой под кнопками; разбора не было — ничего. */
export function JudgePassStatus() {
  const { data: view } = useJudgePass();
  useSettled(view);
  if (view === undefined || view.state === 'idle') return null;
  if (passMoving(view)) return <Going view={view} />;
  if (view.state === 'done') return <Done view={view} />;
  if (view.state === 'stopped') return <Broke view={view} title="Разбор остановлен" />;
  if (view.state === 'failed') return <Broke view={view} title="Разбор оборвался" />;
  return (
    <Text size="sm" c="dimmed" role="status">
      Очередь не отвечает: ход разбора сейчас не узнать.
    </Text>
  );
}
