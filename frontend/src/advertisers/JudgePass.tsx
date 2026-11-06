/**
 * «Переобойти спорные»: судья заново смотрит страницу со ссылкой и сайт рекламодателя и ставит совет.
 * Смета и подтверждение, ход «X из Y», итог словами (`api/advertisers/judge_pass.py`, по образцу
 * пересуда продаж `sales/SegmentSitesRejudge`).
 *
 * **Смета — всегда свежая** и открывается и при нуле: «к разбору 0, уже разобраны N» отвечает на
 * «почему не работает» лучше выключенной кнопки. Запуск — только по той смете, что видел человек;
 * отказ сервера — в окне, над кнопками.
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

import { Alert, Button, Group, List, Loader, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchJudgePass, startJudgePass } from '../api/judgePass';
import type { JudgePassView } from '../api/judgePass';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
import { notify } from '../notices';

const JUDGE_PASS_KEY = ['advertisers-judge-pass'] as const;
/** Очередь проверки: совет судьи виден в ней под решением. */
const QUEUE_KEY = ['advertisers'] as const;
const POLL_MS = 5_000;

const INTRO =
  'Судья заново откроет страницу со ссылкой и сайт рекламодателя и поставит совет. Решения людей, ' +
  'очередь и письма не меняются: совет виден только под решением.';

function moving(view: JudgePassView | undefined): boolean {
  return view?.state === 'queued' || view?.state === 'running';
}

function resumes(view: JudgePassView | undefined): boolean {
  return view?.state === 'stopped' || view?.state === 'failed';
}

function useJudgePass() {
  return useQuery({
    queryKey: JUDGE_PASS_KEY,
    queryFn: fetchJudgePass,
    refetchInterval: (query) => (moving(query.state.data) ? POLL_MS : false),
  });
}

/** Смета в окне: что считается, почему не посчиталась, строки сервера и его отказ. */
function Estimate({ view, error }: { view: JudgePassView | undefined; error: Error | null }) {
  if (error !== null) {
    return (
      <Alert color="red" title="Смета не посчиталась">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (view === undefined) return <Loader aria-label="Считаем смету разбора" size="sm" />;
  return (
    <>
      <List size="sm" spacing={4} aria-label="Смета разбора">
        {view.estimate.lines.map((line) => (
          <List.Item key={line}>{line}</List.Item>
        ))}
      </List>
      {view.estimate.refusal !== null && (
        <Alert color="red" title="Разбор не запустить">
          {view.estimate.refusal}
        </Alert>
      )}
    </>
  );
}

function JudgePassModal({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  // Смета — своим ключом и свежая на каждое открытие: могли прийти новые спорные и выбрать потолок.
  const found = useQuery({
    queryKey: [...JUDGE_PASS_KEY, 'estimate'],
    queryFn: fetchJudgePass,
    gcTime: 0,
  });
  const start = useMutation({
    mutationFn: startJudgePass,
    onSuccess: (view) => {
      client.setQueryData(JUDGE_PASS_KEY, view);
      notify({ message: 'Разбор спорных поставлен', color: 'green' });
      onClose();
    },
  });
  const view = found.data;
  const cases = view?.estimate.cases ?? 0;
  const ready = view !== undefined && view.estimate.refusal === null && cases > 0 && !moving(view);
  return (
    <Modal opened onClose={onClose} title="Переобойти спорные" size="lg">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          {INTRO}
        </Text>
        {start.error !== null && (
          <Alert color="red" title="Разбор не запущен">
            {refusalOf(start.error)}
          </Alert>
        )}
        <Estimate view={view} error={found.error} />
        <Group justify="flex-end" gap="sm">
          <Button variant="subtle" className="press" disabled={start.isPending} onClick={onClose}>
            Отмена
          </Button>
          <Button
            className="press"
            disabled={!ready}
            loading={start.isPending}
            onClick={() => start.mutate(cases)}
          >
            {resumes(view) ? 'Продолжить' : 'Переобойти'} · {formatNumber(cases)}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
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
        disabled={moving(view)}
        onClick={() => setOpened(true)}
      >
        {resumes(view) ? 'Продолжить разбор' : 'Переобойти спорные'}
      </Button>
      {opened ? <JudgePassModal onClose={() => setOpened(false)} /> : null}
    </>
  );
}

/** Кончился разбор — очередь перечитывается: под решениями появились новые советы. */
function useSettled(view: JudgePassView | undefined) {
  const client = useQueryClient();
  const was = useRef(false);
  const now = moving(view);
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
  if (moving(view)) return <Going view={view} />;
  if (view.state === 'done') return <Done view={view} />;
  if (view.state === 'stopped') return <Broke view={view} title="Разбор остановлен" />;
  if (view.state === 'failed') return <Broke view={view} title="Разбор оборвался" />;
  return (
    <Text size="sm" c="dimmed" role="status">
      Очередь не отвечает: ход разбора сейчас не узнать.
    </Text>
  );
}
