/**
 * Неизвестный адрес — внутри рамы, с объяснением и дорогой назад.
 *
 * До 25.09.2026 такой адрес давал пустое полотно без шапки и меню (аудит,
 * №4): человек, пришедший по устаревшей ссылке коллеги или ошибившийся
 * в адресе, видел не отказ, а сломанный сервис. Пустой экран объясняет,
 * чего на нём нет и почему, — правило то же, что у пустых списков.
 */

import { Anchor, Card, Code, Stack, Text, Title } from '@mantine/core';
import { Link, useLocation } from 'react-router-dom';

export function NotFoundPage() {
  const { pathname } = useLocation();

  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="sm">
        <Title order={3}>Такой страницы нет</Title>
        <Text>
          Адрес <Code>{pathname}</Code> сервису не знаком: ссылка устарела или в ней опечатка.
          Разделы — в меню слева.
        </Text>
        <Anchor component={Link} to="/" fw={500}>
          На главную
        </Anchor>
      </Stack>
    </Card>
  );
}
