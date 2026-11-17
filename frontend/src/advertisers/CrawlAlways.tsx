/**
 * «Обход 24/7» — блок на вкладке «Обход доноров», рядом со строкой ночного обхода (17.11.2026).
 *
 * Слово другой стороны: обход доноров идёт постоянно, кнопкой старт/пауза. Сначала — сайты, которых
 * ещё не обходили, потом по 10 000 страниц каждого, дальше — ещё по 10 000, первыми доходные. Сайт,
 * у которого на 10 000 страниц меньше порога страниц с платной ссылкой, получает пометку «мало
 * рекламодателей» и дальше не идёт, пока пометку не снимут руками или не включат переобход слабых.
 * Ночь (00:00–07:00) остаётся ночной; пауза останавливает только 24/7 (решение владельца 17.11).
 *
 * **Итог — одной строкой** (`docs/UI_RULES.md`): «Обход 24/7 идёт: …» — словами сервера,
 * подробности — в «i» его строками. Включённый и не идущий — почему, строкой ниже:
 * «Сейчас не идёт: …».
 *
 * **Запуск — с вопросом у кнопки** (`ConfirmPopover`): что 24/7 сделает, что ночь остаётся ночной,
 * что юниты Ahrefs и потолок платного обхода действуют как сейчас и что пауза — одним нажатием.
 * **Пауза — одним нажатием**: остановить не должно быть труднее, чем запустить. Порог и переобход
 * слабых — ниже, своим компонентом (`CrawlAlwaysSettings`).
 *
 * Без права запуска — строки без кнопок; рубильник окружения выключен — одна строка; не загрузилось
 * — строка отказа, а не пустое место. Пока 24/7 включён — блок перечитывается сам, а сменилось
 * число идущих частей — перечитывается и сводка обхода, которая ведёт таблицу доноров.
 */

import { Alert, Button, Card, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { refusalOf } from '../api/client';
import type { AlwaysView } from '../api/crawlAlways';
import { ALWAYS_KEY, fetchAlways, setAlways } from '../api/crawlAlways';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { HintLines } from '../components/HintLines';
import { InfoHint } from '../components/InfoHint';
import { AlwaysSettings } from './CrawlAlwaysSettings';
import type { AlwaysChanger } from './CrawlAlwaysSettings';
import {
  alwaysHint,
  changeTitle,
  doneWords,
  START_WARNING,
  UNAVAILABLE,
  waitingWords,
  watched,
} from './crawlAlwaysWords';
import { CRAWL_SUMMARY_KEY } from './crawlQueries';

/** Пока 24/7 включён, строка перечитывается сама: части идут часами, начинаются и кончаются. */
const POLL_MS = 30_000;

function useAlways() {
  return useQuery({
    queryKey: ALWAYS_KEY,
    queryFn: fetchAlways,
    refetchInterval: (query) => (watched(query.state.data) ? POLL_MS : false),
  });
}

/** Сменилось число идущих частей 24/7 — перечитывается сводка обхода: она опрашивает сервер, пока
 *  что-то идёт, и перечитывает таблицу доноров (`useCrawlSummary`). Без этого запущенный 24/7 не
 *  был бы виден в таблице до перезагрузки. */
function useRunningEcho(running: number | undefined) {
  const client = useQueryClient();
  const before = useRef(running);
  useEffect(() => {
    if (before.current !== undefined && running !== before.current) {
      void client.invalidateQueries({ queryKey: CRAWL_SUMMARY_KEY });
    }
    before.current = running;
  }, [running, client]);
}

/** Смена настроек: ответ сервера — новое состояние блока; отказ — перечитать (рубильник могли
 *  выключить, и 409 говорит об этом). */
function useChange(): AlwaysChanger {
  const client = useQueryClient();
  return useMutation({
    mutationFn: setAlways,
    onSuccess: (view) => client.setQueryData(ALWAYS_KEY, view),
    onError: () => void client.invalidateQueries({ queryKey: ALWAYS_KEY }),
  });
}

/** «Обход 24/7 идёт: …» — словами сервера; подробности — в «i» его строками. */
function StateLine({ view }: { view: AlwaysView }) {
  const hint = alwaysHint(view);
  return (
    <Group gap={4} align="center" wrap="nowrap">
      <Text size="sm" c={view.enabled ? 'inherit' : 'dimmed'}>
        <Text span inherit fw={500}>
          Обход 24/7
        </Text>{' '}
        {view.words}
      </Text>
      {hint.length > 0 ? (
        <InfoHint name="Подробнее об обходе 24/7" width={340} interactive>
          <HintLines lines={hint} />
        </InfoHint>
      ) : null}
    </Group>
  );
}

/** Запуск — после вопроса у кнопки, пауза — сразу. */
function Toggle({ view, change }: { view: AlwaysView; change: AlwaysChanger }) {
  const busy = change.isPending;
  const asked = change.variables?.enabled;
  if (view.enabled) {
    return (
      <Button
        size="compact-sm"
        variant="default"
        className="press"
        loading={busy && asked === false}
        disabled={busy}
        onClick={() => change.mutate({ enabled: false })}
      >
        Пауза
      </Button>
    );
  }
  return (
    <ConfirmPopover
      message={START_WARNING}
      confirm="Запустить"
      onConfirm={() => change.mutate({ enabled: true })}
    >
      {(ask) => (
        <Button
          size="compact-sm"
          variant="light"
          className="press"
          loading={busy && asked === true}
          disabled={busy}
          onClick={ask}
        >
          Запустить 24/7…
        </Button>
      )}
    </ConfirmPopover>
  );
}

/** Почему включённый не идёт и когда пройдено всё — строками под итогом, если есть что сказать. */
function Status({ view }: { view: AlwaysView }) {
  const waiting = waitingWords(view);
  const done = doneWords(view);
  return (
    <>
      {waiting !== null ? <Text size="sm">{waiting}</Text> : null}
      {done !== null ? (
        <Text size="sm" c="dimmed">
          {done}
        </Text>
      ) : null}
    </>
  );
}

function AlwaysCard({ view, mayRun }: { view: AlwaysView; mayRun: boolean }) {
  const change = useChange();
  return (
    <Card className="glassQuiet" p="sm">
      <Stack gap={6} role="group" aria-label="Обход 24/7">
        <Group justify="space-between" align="center" gap="xs">
          <StateLine view={view} />
          {mayRun ? <Toggle view={view} change={change} /> : null}
        </Group>
        <Status view={view} />
        {change.error !== null ? (
          <Alert color="red" title={changeTitle(change.variables)}>
            {refusalOf(change.error)}
          </Alert>
        ) : null}
        <AlwaysSettings view={view} change={change} mayRun={mayRun} />
      </Stack>
    </Card>
  );
}

export function CrawlAlways({
  mayRun,
}: {
  /** Право запуска: без него — строки без кнопок, поля и переключателя. */
  mayRun: boolean;
}) {
  const { data, error } = useAlways();
  useRunningEcho(data?.counts.running);
  if (data === undefined) {
    // Пропавший молча блок читался бы как «24/7 нет», а не «не загрузилось».
    return error ? (
      <Text size="sm" c="dimmed">
        Обход 24/7 не загрузился: {refusalOf(error)}
      </Text>
    ) : null;
  }
  if (!data.available) {
    return (
      <Text size="sm" c="dimmed">
        {UNAVAILABLE}
      </Text>
    );
  }
  return <AlwaysCard view={data} mayRun={mayRun} />;
}
