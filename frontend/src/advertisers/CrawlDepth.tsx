/**
 * Обход 24/7 у донора — строкой под значком последнего обхода на вкладке «Обход доноров»
 * (17.11.2026): сколько страниц прошёл 24/7 и идёт ли сейчас, пометка «мало рекламодателей» и,
 * в «i», доля страниц с платной ссылкой и кто поставил пометку — сервис по порогу или человек.
 *
 * **Пометка руками — кнопками в том же «i»** (только с правом запуска): «Снять пометку» — сайт идёт
 * дальше при любом пороге, «Пометить» — 24/7 его дальше не ведёт, «Вернуть оценку сервису» — снова
 * решает порог. Действие редкое, а строка таблицы — в одну высоту: кнопки в строке растили бы её
 * у каждого донора. После нажатия «i» не закрывается и показывает новое состояние.
 *
 * Строки нет, пока 24/7 донора не трогал, и у ответа сервера, который о 24/7 не знает (`depth`
 * нет): таблица выглядит как до 24/7.
 */

import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { AlwaysView, DonorDepth, WeakMark } from '../api/crawlAlways';
import { ALWAYS_KEY, fetchAlways, markWeak } from '../api/crawlAlways';
import { refusalOf } from '../api/client';
import type { CrawlDonorsView } from '../api/crawls';
import { useSession } from '../auth/AuthProvider';
import { HintLines } from '../components/HintLines';
import { InfoHint } from '../components/InfoHint';
import { depthLines, depthShown, depthWords, markActions } from './crawlAlwaysWords';
import { WEAK_TITLE } from './crawlFilters';
import { CRAWL_DONORS_KEY } from './crawlQueries';

/** Строки страниц доноров с новым состоянием донора — сразу, до перечитанной страницы. */
function withDepth(page: CrawlDonorsView, host: string, depth: DonorDepth): CrawlDonorsView {
  return { ...page, rows: page.rows.map((row) => (row.host === host ? { ...row, depth } : row)) };
}

/** Пометка руками: ответ — новое состояние донора; перечитываются страница доноров (под фильтром
 *  «мало рекламодателей» донор мог из неё уйти) и блок 24/7 (его числа). */
function useMark(host: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (weak: WeakMark) => markWeak(host, weak),
    onSuccess: (depth) => {
      client.setQueriesData<CrawlDonorsView>({ queryKey: CRAWL_DONORS_KEY }, (page) =>
        page === undefined ? page : withDepth(page, host, depth),
      );
      void client.invalidateQueries({ queryKey: CRAWL_DONORS_KEY });
      void client.invalidateQueries({ queryKey: ALWAYS_KEY });
    },
  });
}

function MarkButtons({ host, depth }: { host: string; depth: DonorDepth }) {
  const mark = useMark(host);
  return (
    <Stack gap={4}>
      <Group gap="xs">
        {markActions(depth).map((action) => (
          <Button
            key={action.label}
            size="compact-sm"
            variant="default"
            className="press"
            loading={mark.isPending && mark.variables === action.weak}
            disabled={mark.isPending}
            onClick={() => mark.mutate(action.weak)}
          >
            {action.label}
          </Button>
        ))}
      </Group>
      {mark.error !== null ? (
        <Text size="sm" c="var(--mantine-color-error)" role="alert">
          Пометка не сменилась: {refusalOf(mark.error)}
        </Text>
      ) : null}
    </Stack>
  );
}

/** Порог блока 24/7 — из уже загруженного ответа, без своего запроса: по нему доля у края порога
 *  пишется с той точностью, при которой не перескакивает его. */
function useThreshold(): number | undefined {
  const { data } = useQuery<AlwaysView>({
    queryKey: ALWAYS_KEY,
    queryFn: fetchAlways,
    enabled: false,
  });
  return data?.threshold_pct;
}

function DepthHint({ host, depth }: { host: string; depth: DonorDepth }) {
  const { can } = useSession();
  const threshold = useThreshold();
  return (
    <Stack gap="xs">
      <HintLines lines={depthLines(depth, threshold)} />
      {can('run') ? <MarkButtons host={host} depth={depth} /> : null}
    </Stack>
  );
}

/** Пометка строкой: стоит — значком, как пометки строк у доноров; снята руками — тихо словами:
 *  порог к сайту не применяется. */
function WeakLabel({ depth }: { depth: DonorDepth }) {
  if (depth.weak) {
    return (
      <Badge size="sm" variant="light" color="yellow">
        {WEAK_TITLE}
      </Badge>
    );
  }
  return (
    <Text span size="xs" c="dimmed">
      пометка снята
    </Text>
  );
}

/** Строка 24/7 под значком обхода. «i» держится за то, что стоит перед ним, — за пометку, а без неё
 *  за слова (`crawlDepthTail`): в узкой колонке (1280, телефон) он не уходит на строку один. */
export function CrawlDepth({ host, depth }: { host: string; depth: DonorDepth }) {
  if (!depthShown(depth)) return null;
  const marked = depth.weak || depth.weak_by === 'human';
  const words = (
    <Text span size="xs" c="dimmed">
      {depthWords(depth)}
    </Text>
  );
  return (
    <div className="crawlDepth">
      {marked ? words : null}
      <span className="crawlDepthTail">
        {marked ? <WeakLabel depth={depth} /> : words}
        <InfoHint name={`Обход 24/7: ${host}`} width={320} interactive>
          <DepthHint host={host} depth={depth} />
        </InfoHint>
      </span>
    </div>
  );
}
