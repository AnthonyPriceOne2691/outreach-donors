/**
 * Правая колонка «Диалогов», пока диалог не выбран (`ThreadsScreen`).
 *
 * Не пустота, а что делать: открыть диалог слева или сразу первый из ждущих
 * человека — ради них экран и открывают. Тот же список и порядок, что слева
 * (`useThreadList`): «первый ждущий» — верхняя строка списка.
 */

import { Button, Card, Stack, Text, Title } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { Link, useLocation } from 'react-router-dom';

import { formatNumber } from '../format';
import { useSortedThreads, waitsForPerson } from './useThreadList';

export function ThreadsIntro() {
  const location = useLocation();
  const { threads } = useSortedThreads();
  const waiting = threads.filter((thread) => waitsForPerson(thread.state));
  const first = waiting[0];
  return (
    <Card className="glassPanel threadsIntro" p="xl">
      <Stack gap="sm" align="center" ta="center">
        <Title order={4}>Выберите диалог в списке</Title>
        <Text size="sm" c="dimmed" maw={420}>
          Переписка откроется здесь. Соседний диалог — стрелками ↑ ↓ в списке или клавишами J и K.
        </Text>
        {first !== undefined && (
          <Button
            component={Link}
            to={`/threads/${first.id}${location.search}`}
            variant="light"
            className="press"
            rightSection={<IconArrowRight size={16} />}
          >
            Ждут человека: {formatNumber(waiting.length)} — открыть первый
          </Button>
        )}
      </Stack>
    </Card>
  );
}
