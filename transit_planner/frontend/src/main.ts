import { mount } from "svelte";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";
import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) {
  throw new Error("Корневой элемент приложения не найден");
}

mount(App, { target });
