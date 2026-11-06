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
 */

import { Alert, Button, Group, Loader, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { fetchJudgePass, JUDGE_PASS_KEY, passMoving, passResumes } from '../api/judgePass';
import type { JudgePassView } from '../api/judgePass';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
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

function Done({ view }: { view: JudgePassView }) {
  const when = view.ended_at !== null ? ` ${formatDateTime(view.ended_at)}` : '';
  const who = view.by !== null ? ` · ${view.by}` : '';
  return (
    <Text size="sm" c="dimmed" role="status">
      Разбор окончен{when}
      {who}: {view.lines.join(' · ') || 'разбирать было некого'}
    </Text>
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
