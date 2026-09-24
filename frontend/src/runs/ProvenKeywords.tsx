/**
 * Ключи страны, дававшие принятых доноров, — подсказка к списку ключей.
 *
 * Обратная связь прогонов: следующий набор собирается из ключей, которые
 * уже приносили доноров, а не из всех подряд. Считает сервер — по всем
 * прогонам страны, домен по одному разу и с последним решением человека.
 *
 * **Добавляет, а не заменяет.** Кнопка дописывает в поле только те ключи,
 * которых там ещё нет: свой список оператора остаётся его списком.
 * Нет таких ключей — подсказки нет вовсе, а не пустой блок.
 */

import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchKeywordYield } from '../api/keywords';
import { countryTitle } from '../api/labels';

/** Сколько ключей показывать и добавлять разом. */
const SHOWN = 12;

interface Props {
  country: string;
  /** Ключи, уже стоящие в поле. */
  current: string[];
  onAdd: (keywords: string[]) => void;
}

function same(keyword: string): string {
  return keyword.toLowerCase().split(/\s+/).filter(Boolean).join(' ');
}

export function ProvenKeywords({ country, current, onAdd }: Props) {
  const { data } = useQuery({
    queryKey: ['keywords-yield', country],
    queryFn: () => fetchKeywordYield(country),
  });
  const rows = (data ?? []).slice(0, SHOWN);
  if (rows.length === 0) return null;

  const have = new Set(current.map(same));
  const missing = rows.filter((row) => !have.has(same(row.keyword)));

  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        Ключи, дававшие принятых доноров · {countryTitle(country)}
      </Text>
      <Group gap={6}>
        {rows.map((row) => (
          <Badge key={row.keyword} variant="light" color="lagoon" tt="none">
            {row.keyword} · принято {row.accepted}
          </Badge>
        ))}
      </Group>
      <Group>
        <Button
          variant="light"
          className="press"
          disabled={missing.length === 0}
          onClick={() => onAdd(missing.map((row) => row.keyword))}
        >
          {missing.length === 0 ? 'Все уже в списке' : `Добавить в список — ${missing.length}`}
        </Button>
      </Group>
    </Stack>
  );
}
