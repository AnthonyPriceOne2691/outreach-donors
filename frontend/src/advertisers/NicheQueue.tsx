/**
 * Бизнесы ниши из выдачи прогона: сайты, которые сами продают в нише,
 * — не доноры, а кандидаты в рекламодатели (решение Anthony 04.10.2026).
 *
 * Человек решает «пишем / не пишем»: до «пишем» ни поиск адреса, ни письмо
 * бизнес не трогают. Для прогонов, прошедших до сбора, — «Собрать из
 * прогона»: вердикты судьи там уже оплачены.
 *
 * **По двадцать на странице** (слово Anthony 10.10.2026): полсотни строк
 * «пишем / не пишем» одним списком были простынёй, а пятьдесят первый бизнес —
 * недостижим. Номер страницы — в адресе (`?niche_page=2`), размер называет сервер;
 * решённый бизнес уходит из очереди, и страница добирает следующий.
 */

import { Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import type { NicheCard } from '../api/niche';
import { useSession } from '../auth/AuthProvider';
import { useNicheCollect, useNicheQueue } from './useNiche';
import { InfoHint } from '../components/InfoHint';
import { NumberField } from '../components/NumberField';
import { PageSwitch, usePageParam } from '../components/PageSwitch';
import { numberRefusal, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';

/** Номер прогона — целое с единицы. Набранное стоит в поле как есть: «1.5» не
 *  склеивается в 15, на которое сервер честно отвечал «Прогона №15 нет», а
 *  получает отказ (проверка QA 10.10.2026). */
const RUN_NUMBER: NumberRule = { decimals: 0, min: 1 };

function whereFrom(card: NicheCard): string {
  const run = card.run_id === null ? 'прогон удалён' : `прогон №${card.run_id}`;
  const keys = card.keywords.slice(0, 3).join(', ');
  const country = card.country ? card.country.toUpperCase() : null;
  return [run, keys, country].filter(Boolean).join(' · ');
}

function NicheRow({
  card,
  canDecide,
  busy,
  onDecide,
}: {
  card: NicheCard;
  canDecide: boolean;
  busy: boolean;
  onDecide: (write: boolean) => void;
}) {
  return (
    <Stack gap={4} className="nicheRow">
      <Group justify="space-between" gap="xs">
        <Text fw={500}>{card.host}</Text>
        {canDecide && (
          <Group gap="xs">
            <Button
              size="compact-sm"
              className="press"
              loading={busy}
              onClick={() => onDecide(true)}
            >
              Пишем
            </Button>
            <Button
              size="compact-sm"
              variant="subtle"
              color="gray"
              className="press"
              disabled={busy}
              onClick={() => onDecide(false)}
            >
              Не пишем
            </Button>
          </Group>
        )}
      </Group>
      <Text size="sm" c="dimmed">
        {whereFrom(card)}
      </Text>
      <Group gap="xs">
        <Badge variant="light" color={card.intent_by === 'human' ? 'green' : 'lagoon'}>
          {card.intent_by === 'human' ? 'продаёт своё — сказал человек' : 'продаёт своё — судья'}
        </Badge>
        {card.quote && (
          <Text size="sm" className="nicheQuote">
            «{card.quote}»
          </Text>
        )}
      </Group>
    </Stack>
  );
}

function Collect() {
  const [runId, setRunId] = useState('');
  const collect = useNicheCollect();
  const run = validNumber(runId, RUN_NUMBER);
  return (
    <Group gap="xs" align="flex-end">
      <NumberField
        label="Собрать из прогона №"
        w="12rem"
        refusalAbove
        value={runId}
        error={numberRefusal(runId, RUN_NUMBER)}
        onChange={setRunId}
      />
      <Button
        variant="light"
        className="press"
        loading={collect.isPending}
        disabled={run === null}
        onClick={() => run !== null && collect.mutate(run)}
      >
        Собрать
      </Button>
    </Group>
  );
}

/** Имя страницы в адресе: листает карточка, а не весь экран рекламодателей. */
const PAGE_PARAM = 'niche_page';

export function NicheQueue() {
  const { can } = useSession();
  const [page, goToPage] = usePageParam(PAGE_PARAM);
  const { queue, decide } = useNicheQueue(page);
  const { data, error, isPlaceholderData } = queue;
  const rows = data?.rows ?? [];
  const pages = data === undefined ? 1 : Math.max(1, Math.ceil(data.total / data.limit));
  const settled = data !== undefined && !isPlaceholderData;
  const top = useRef<HTMLDivElement>(null);
  const calm = useReducedMotion();

  // Страница за концом — решён последний бизнес последней страницы или открыта старая
  // ссылка: сервер отдаёт её пустой и называет, сколько всего, — экран уходит на последнюю,
  // заменяя адрес, а не добавляя.
  useEffect(() => {
    if (settled && page > pages) goToPage(pages, true);
  }, [settled, page, pages, goToPage]);

  // Переключатель — под двадцатью строками: новая страница начинается с заголовка
  // карточки, а не с её низа, где человек нажал номер. Плавно — кроме тех, кто
  // просил систему обходиться без движения.
  const turn = (next: number) => {
    goToPage(next);
    top.current?.scrollIntoView({ block: 'start', behavior: calm ? 'auto' : 'smooth' });
  };

  return (
    <Card className="glass" p="xl" ref={top}>
      <Stack gap="md">
        <Stack gap={6}>
          <Group gap="xs">
            <Title order={4}>Бизнесы ниши из выдачи</Title>
            <InfoHint name="Что такое бизнесы ниши" width={340}>
              Сайты, которые сами продают в нише прогона: не доноры, а кандидаты в рекламодатели.
              Пока не решили «пишем», адрес им не ищут и писем не пишут.
            </InfoHint>
          </Group>
          {data !== undefined && <Text size="sm">{`Ждут решения: ${data.waiting}.`}</Text>}
        </Stack>
        {error ? (
          <Text size="sm" c="red">
            {refusalOf(error)}
          </Text>
        ) : data?.total === 0 ? (
          // «Нет» — по числу сервера, а не по пустой странице: страница за концом пуста,
          // хотя ждущие есть, и экран сейчас уйдёт на последнюю.
          <Text size="sm" c="dimmed">
            Ждущих решения нет: бизнесы ниши собираются в конце каждого прогона.
          </Text>
        ) : (
          <Stack gap="md">
            {rows.map((card) => (
              <NicheRow
                key={card.id}
                card={card}
                canDecide={can('prices')}
                busy={decide.isPending && decide.variables?.id === card.id}
                onDecide={(write) => decide.mutate({ id: card.id, write })}
              />
            ))}
            <PageSwitch
              label="Страницы бизнесов ниши"
              page={page}
              pages={pages}
              onChange={turn}
              pt="xs"
            />
          </Stack>
        )}
        {can('prices') && <Collect />}
      </Stack>
    </Card>
  );
}
